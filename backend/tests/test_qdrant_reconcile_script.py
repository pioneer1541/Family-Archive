import sqlite3
from pathlib import Path

from scripts import reconcile_qdrant_points as reconcile


class _FakeResp:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if int(self.status_code) >= 400:
            raise RuntimeError(f"http_{self.status_code}")

    def json(self):
        return self._payload


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("create table chunks (id text primary key, document_id text not null)")
        conn.execute("insert into chunks(id, document_id) values (?, ?)", ("chunk-1", "doc-1"))
        conn.execute("insert into chunks(id, document_id) values (?, ?)", ("chunk-2", "doc-2"))
        conn.commit()
    finally:
        conn.close()


def test_reconcile_qdrant_points_dry_run(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "reconcile.db"
    _seed_db(db_path)
    scroll_points = [
        {"id": "p-keep", "payload": {"doc_id": "doc-1", "chunk_id": "chunk-1"}},
        {"id": "p-missing", "payload": {"doc_id": "doc-1", "chunk_id": "chunk-x"}},
        {"id": "p-mismatch", "payload": {"doc_id": "doc-x", "chunk_id": "chunk-2"}},
        {"id": "p-bad", "payload": {"doc_id": "doc-3"}},
    ]

    def _fake_post(url, json=None, timeout=0):
        if "/points/scroll" in str(url):
            return _FakeResp({"result": {"points": scroll_points, "next_page_offset": None}})
        raise AssertionError("dry-run should not call delete endpoint")

    monkeypatch.setattr(reconcile.requests, "post", _fake_post)
    out = reconcile.reconcile_qdrant_points(
        db_path=str(db_path),
        qdrant_url="http://unit-qdrant:6333",
        collection="fkv_docs_v1",
        apply=False,
    )
    assert out["apply"] is False
    assert int(out["scanned_points"]) == 4
    assert int(out["kept_points"]) == 1
    assert int(out["orphan_points"]) == 3
    assert int(out["deleted_points"]) == 0
    reasons = out.get("orphan_reasons") or {}
    assert int(reasons.get("missing_chunk") or 0) == 1
    assert int(reasons.get("doc_mismatch") or 0) == 1
    assert int(reasons.get("missing_payload_keys") or 0) == 1


def test_reconcile_qdrant_points_apply(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "reconcile_apply.db"
    _seed_db(db_path)
    captured = {"delete_calls": []}

    def _fake_post(url, json=None, timeout=0):
        text = str(url)
        if "/points/scroll" in text:
            points = [
                {"id": "p-keep", "payload": {"doc_id": "doc-1", "chunk_id": "chunk-1"}},
                {"id": "p-delete", "payload": {"doc_id": "doc-1", "chunk_id": "chunk-x"}},
            ]
            return _FakeResp({"result": {"points": points, "next_page_offset": None}})
        if "/points/delete" in text:
            captured["delete_calls"].append(list((json or {}).get("points") or []))
            return _FakeResp({"status": "ok", "result": {}})
        raise AssertionError(f"unexpected_url:{url}")

    monkeypatch.setattr(reconcile.requests, "post", _fake_post)
    out = reconcile.reconcile_qdrant_points(
        db_path=str(db_path),
        qdrant_url="http://unit-qdrant:6333",
        collection="fkv_docs_v1",
        apply=True,
        delete_batch_size=100,
    )
    assert out["apply"] is True
    assert int(out["orphan_points"]) == 1
    assert int(out["deleted_points"]) == 1
    assert int(out["delete_batches"]) == 1
    assert captured["delete_calls"] == [["p-delete"]]
