from evaluation import agent_eval_scoring as scoring


def test_boundary_refusal_scores_full(monkeypatch):
    case = {
        "id": "X01",
        "domain": "bills",
        "should_refuse": True,
        "keywords_expected": ["没有相关信息"],
    }
    monkeypatch.setattr(scoring, "judge_with_llm", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("skip_judge")))
    out = scoring.score_case_mixed(
        case=case,
        answer="资料中没有相关信息，暂时无法确认。",
        related_docs=[],
        judge_model="qwen3:4b-instruct",
    )
    assert float(out["mixed"]["overall"]) >= 0.99


def test_boundary_hallucination_scores_zero():
    case = {"id": "X02", "domain": "insurance", "should_refuse": True}
    out = scoring.score_case_mixed(
        case=case,
        answer="有，保单号123456，金额890澳币。",
        related_docs=[],
        judge_model="qwen3:4b-instruct",
    )
    assert float(out["mixed"]["overall"]) == 0.0
    assert float(out["mixed"]["answer_faithfulness"]) == 0.0


def test_mixed_scoring_formula(monkeypatch):
    case = {"id": "X03", "domain": "bills", "should_refuse": False, "keywords_expected": ["金额"]}

    def _fake_judge(**kwargs):
        return {
            "context_relevance": 0.8,
            "answer_faithfulness": 0.7,
            "answer_relevance": 0.9,
            "rationale": "ok",
        }

    monkeypatch.setattr(scoring, "judge_with_llm", _fake_judge)
    out = scoring.score_case_mixed(
        case=case,
        answer="金额是109澳币。",
        related_docs=[{"category_path": "finance/bills/internet", "title": "互联网账单"}],
        judge_model="unit-test-model",
    )
    mixed = out["mixed"]
    assert float(mixed["overall"]) > 0.0
    assert float(mixed["context_relevance"]) > 0.0
    assert out["judge"] is not None
