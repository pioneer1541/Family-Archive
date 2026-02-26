import json
from pathlib import Path

from evaluation import run_agent_eval
from evaluation import run_agent_eval_trend


def test_run_eval_generates_rows(monkeypatch, tmp_path: Path):
    cases = {
        "version": "v1",
        "cases": [
            {
                "id": "B01",
                "domain": "bills",
                "type": "fact",
                "difficulty": 1,
                "question_zh": "上个月电费多少",
                "expected_behavior": "返回金额",
                "should_refuse": False,
                "keywords_expected": ["金额"],
            },
            {
                "id": "I07",
                "domain": "insurance",
                "type": "boundary",
                "difficulty": 1,
                "question_zh": "我们有人寿保险吗",
                "expected_behavior": "拒答",
                "should_refuse": True,
            },
        ],
    }
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    def _fake_call_agent_execute(**kwargs):
        q = str(kwargs.get("question") or "")
        if "人寿保险" in q:
            return {"card": {"short_summary": {"zh": "资料中没有相关信息。"}, "key_points": []}, "related_docs": []}
        return {
            "card": {"short_summary": {"zh": "金额是109澳币。"}, "key_points": [{"zh": "到期日是2月23日"}]},
            "related_docs": [{"doc_id": "d1", "title_zh": "互联网账单", "category_path": "finance/bills/internet"}],
            "executor_stats": {"route": "detail_extract"},
        }

    def _fake_score_case_mixed(**kwargs):
        return {
            "rule": {"context_relevance": 1.0, "answer_faithfulness": 1.0, "answer_relevance": 1.0, "rule_notes": []},
            "judge": {"context_relevance": 1.0, "answer_faithfulness": 1.0, "answer_relevance": 1.0, "rationale": "ok"},
            "judge_error": "",
            "mixed": {"context_relevance": 1.0, "answer_faithfulness": 1.0, "answer_relevance": 1.0, "overall": 1.0},
        }

    monkeypatch.setattr(run_agent_eval, "call_agent_execute", _fake_call_agent_execute)
    monkeypatch.setattr(run_agent_eval, "score_case_mixed", _fake_score_case_mixed)

    report = run_agent_eval.run_eval(
        api="http://127.0.0.1:18180",
        cases_path=str(cases_path),
        sample_size=2,
        boundary_cases_path=None,
        boundary_sample_size=0,
        seed=7,
        ui_lang="zh",
        judge_model="unit-model",
        judge_timeout_sec=1,
    )
    assert report["sample_size"] == 2
    assert len(report["rows"]) == 2
    assert "avg_total_score" in report["summary"]
    assert "summary_excluding_infra" in report
    assert report["summary_excluding_infra"]["infra_error_count"] == 0


def test_trend_aggregates_reports(tmp_path: Path):
    report1 = {
        "run_id": "r1",
        "generated_at": "2026-01-01T00:00:00+0000",
        "sample_size": 20,
        "summary": {
            "avg_total_score": 0.7,
            "context_relevance_avg": 0.7,
            "answer_faithfulness_avg": 0.7,
            "answer_relevance_avg": 0.7,
            "boundary_refusal_pass_rate": 0.9,
        },
        "rows": [{"id": "B01", "domain": "bills", "scores": {"mixed": {"overall": 0.6}}}],
    }
    report2 = {
        "run_id": "r2",
        "generated_at": "2026-01-02T00:00:00+0000",
        "sample_size": 20,
        "summary": {
            "avg_total_score": 0.8,
            "context_relevance_avg": 0.8,
            "answer_faithfulness_avg": 0.8,
            "answer_relevance_avg": 0.8,
            "boundary_refusal_pass_rate": 1.0,
        },
        "rows": [{"id": "B01", "domain": "bills", "scores": {"mixed": {"overall": 0.9}}}],
    }
    p1 = tmp_path / "agent_eval_report_a.json"
    p2 = tmp_path / "agent_eval_report_b.json"
    p1.write_text(json.dumps(report1, ensure_ascii=False), encoding="utf-8")
    p2.write_text(json.dumps(report2, ensure_ascii=False), encoding="utf-8")

    reports = run_agent_eval_trend._load_reports(str(tmp_path / "agent_eval_report_*.json"))
    trend = run_agent_eval_trend.build_trend(reports)
    assert int(trend["snapshot_count"]) == 2
    assert len(trend["runs"]) == 2
    assert len(trend["top_failures"]) >= 1


def test_summary_excluding_infra_filters_connection_errors():
    rows = [
        {
            "id": "X1",
            "scores": {"mixed": {"overall": 1.0, "context_relevance": 1.0, "answer_faithfulness": 1.0, "answer_relevance": 1.0}, "rule": {"boundary_ok": True}},
            "should_refuse": False,
            "executor_stats": {"route": "search_bundle", "coverage_ratio": 1.0},
            "domain": "bills",
            "error_type": "",
            "infra_error": False,
        },
        {
            "id": "X2",
            "scores": {"mixed": {"overall": 0.0, "context_relevance": 0.0, "answer_faithfulness": 0.0, "answer_relevance": 0.0}, "rule": {"boundary_ok": True}},
            "should_refuse": False,
            "executor_stats": {"route": "", "coverage_ratio": 0.0},
            "domain": "bills",
            "error_type": "ConnectionError",
            "infra_error": True,
        },
    ]
    summary = run_agent_eval.build_summary_excluding_infra(rows)
    assert summary["infra_error_count"] == 1
    assert summary["infra_error_rate"] == 0.5
    assert summary["sample_count_effective"] == 1
    assert summary["avg_total_score"] == 1.0
