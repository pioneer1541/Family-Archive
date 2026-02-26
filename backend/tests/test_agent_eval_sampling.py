from evaluation import run_agent_eval


def test_sampling_same_seed_is_stable():
    cases = [{"id": f"C{i}", "question_zh": f"Q{i}"} for i in range(1, 41)]
    a = run_agent_eval.sample_cases(cases, sample_size=20, seed=1234)
    b = run_agent_eval.sample_cases(cases, sample_size=20, seed=1234)
    assert [x["id"] for x in a] == [x["id"] for x in b]


def test_sampling_diff_seed_changes_order():
    cases = [{"id": f"C{i}", "question_zh": f"Q{i}"} for i in range(1, 41)]
    a = run_agent_eval.sample_cases(cases, sample_size=20, seed=1)
    b = run_agent_eval.sample_cases(cases, sample_size=20, seed=2)
    assert [x["id"] for x in a] != [x["id"] for x in b]


def test_sampling_invalid_size_raises():
    cases = [{"id": "C1", "question_zh": "Q1"}]
    try:
        run_agent_eval.sample_cases(cases, sample_size=2, seed=1)
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_fixed_sampling_keeps_order():
    cases = [{"id": f"B{i}", "question_zh": f"Q{i}"} for i in range(1, 12)]
    picked = run_agent_eval.sample_fixed_cases(cases, sample_size=10)
    assert [x["id"] for x in picked] == [f"B{i}" for i in range(1, 11)]
