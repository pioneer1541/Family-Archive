import datetime as dt
import importlib.util
import json
from pathlib import Path

from app import models
from app.db import SessionLocal
from app.services import ingestion


def _make_doc(path: str, *, status: str, category_path: str) -> models.Document:
    now = dt.datetime.now(dt.UTC)
    return models.Document(
        source_path=path,
        file_name=Path(path).name,
        file_ext="txt",
        file_size=128,
        sha256=f"{abs(hash((path, status, category_path))) % (10**12):012d}".ljust(64, "a")[:64],
        status=status,
        doc_lang="en",
        title_en="Doc",
        title_zh="文档",
        summary_en="",
        summary_zh="",
        category_label_en="Legacy",
        category_label_zh="历史分类",
        category_path=category_path,
        created_at=now,
        updated_at=now - dt.timedelta(days=40),
    )


def test_legacy_category_guard_rewrites_ingestion_result(client, tmp_path: Path, monkeypatch):
    sample = tmp_path / "legacy_category.txt"
    sample.write_text("utility bill sample", encoding="utf-8")

    monkeypatch.setattr(ingestion, "build_document_summaries", lambda **_kwargs: ("summary en", "摘要中文"))
    monkeypatch.setattr(ingestion, "classify_category_from_summary", lambda **_kwargs: ("Legacy", "历史", "general"))
    monkeypatch.setattr(ingestion, "regenerate_friendly_name_from_summary", lambda **_kwargs: ("Legacy Doc", "历史文档"))

    r = client.post("/v1/ingestion/jobs", json={"file_paths": [str(sample)]})
    assert r.status_code == 200
    assert r.json()["success_count"] == 1

    rows = client.get("/v1/documents?status=completed&limit=20&offset=0").json().get("items") or []
    hit = next(item for item in rows if item["file_name"] == "legacy_category.txt")
    rd = client.get(f"/v1/documents/{hit['doc_id']}")
    assert rd.status_code == 200
    doc = rd.json()
    assert doc["category_path"] == "archive/misc"


def test_governance_category_debt_and_trend_endpoints(client, tmp_path: Path, monkeypatch):
    db = SessionLocal()
    try:
        db.add(_make_doc(str(tmp_path / "legacy-failed.txt"), status="failed", category_path="general"))
        db.add(_make_doc(str(tmp_path / "legacy-dup.txt"), status="duplicate", category_path="finance/utilities"))
        db.add(_make_doc(str(tmp_path / "ok-completed.txt"), status="completed", category_path="archive/misc"))
        db.commit()
    finally:
        db.close()

    debt = client.get("/v1/governance/category-debt")
    assert debt.status_code == 200
    out = debt.json()
    assert out["scope_prod"]["legacy_docs"] == 0
    assert out["scope_audit"]["legacy_docs"] >= 2
    assert int(out["legacy_counts_by_status"].get("failed", 0)) >= 1
    assert int(out["legacy_counts_by_status"].get("duplicate", 0)) >= 1

    snap1 = {
        "snapshot_at": (dt.datetime.now(dt.UTC) - dt.timedelta(days=8)).isoformat(),
        "scope_prod": {"legacy_docs": 0, "total_docs": 3},
        "scope_audit": {"legacy_docs": 10, "total_docs": 20},
    }
    snap2 = {
        "snapshot_at": dt.datetime.now(dt.UTC).isoformat(),
        "scope_prod": {"legacy_docs": 0, "total_docs": 3},
        "scope_audit": {"legacy_docs": 7, "total_docs": 20},
    }
    (tmp_path / "category_debt_snapshot_20260210.json").write_text(json.dumps(snap1), encoding="utf-8")
    (tmp_path / "category_debt_snapshot_20260218.json").write_text(json.dumps(snap2), encoding="utf-8")

    from app.api import routes as routes_module

    monkeypatch.setattr(routes_module, "DATA_DIR", tmp_path)
    trend = client.get("/v1/governance/category-debt/trend?days=30")
    assert trend.status_code == 200
    trend_out = trend.json()
    assert trend_out["snapshot_count"] == 2
    assert trend_out["week_over_week_change"] == -3
    assert len(trend_out["points"]) == 2


def test_cleanup_legacy_nonprod_docs_script_dry_run_and_apply(tmp_path: Path):
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_legacy_nonprod_docs.py"
    spec = importlib.util.spec_from_file_location("cleanup_legacy_nonprod_docs", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    db = SessionLocal()
    try:
        db.add(_make_doc(str(tmp_path / "legacy-failed-old.txt"), status="failed", category_path="general"))
        db.add(_make_doc(str(tmp_path / "legacy-dup-old.txt"), status="duplicate", category_path="finance/telecom"))
        db.add(_make_doc(str(tmp_path / "new-failed.txt"), status="failed", category_path="archive/misc"))
        db.commit()
    finally:
        db.close()

    dry = module.run_cleanup(apply=False, days=30, output=tmp_path / "cleanup_dry.json")
    assert dry["candidate_count"] == 2
    assert dry["deleted_duplicate"] == 1
    assert dry["flagged_failed"] == 1

    db = SessionLocal()
    try:
        dup_exists = db.query(models.Document).filter(models.Document.file_name == "legacy-dup-old.txt").one_or_none()
        failed_doc = db.query(models.Document).filter(models.Document.file_name == "legacy-failed-old.txt").one_or_none()
        assert dup_exists is not None
        assert failed_doc is not None
        assert "legacy_cleanup_candidate" not in str(failed_doc.error_code or "")
    finally:
        db.close()

    applied = module.run_cleanup(apply=True, days=30, output=tmp_path / "cleanup_apply.json")
    assert applied["candidate_count"] == 2

    db = SessionLocal()
    try:
        dup_exists = db.query(models.Document).filter(models.Document.file_name == "legacy-dup-old.txt").one_or_none()
        failed_doc = db.query(models.Document).filter(models.Document.file_name == "legacy-failed-old.txt").one_or_none()
        assert dup_exists is None
        assert failed_doc is not None
        assert "legacy_cleanup_candidate" in str(failed_doc.error_code or "")
    finally:
        db.close()
