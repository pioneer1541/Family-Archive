# Evaluation

## Quick Run (Makefile)
```bash
cd family-vault
make eval-all
```

UI E2E:
```bash
cd family-vault
make e2e-ui
```

## M1 Regression
```bash
python evaluation/run_m1_regression.py --api http://127.0.0.1:18180
```

## Planner/Fallback
```bash
python evaluation/run_planner_eval.py --api http://127.0.0.1:18180
```

## Map-Reduce Summary
```bash
python evaluation/run_map_reduce_eval.py --api http://127.0.0.1:18180
```

## Cross-Language Retrieval
```bash
python evaluation/run_crosslang_eval.py --api http://127.0.0.1:18180
```

## Agent Mixed Eval (40-case bank, random 20 each run)
```bash
python evaluation/run_agent_eval.py \
  --api http://127.0.0.1:18180 \
  --cases evaluation/agent_eval_cases_v1.json \
  --sample-size 20 \
  --seed 20260222 \
  --out evaluation/agent_eval_report.json \
  --md-out evaluation/agent_eval_report.md
```

## Agent Dual-Track Eval (random 20 + fixed boundary 10)
```bash
python evaluation/run_agent_eval.py \
  --api http://127.0.0.1:18180 \
  --cases evaluation/agent_eval_cases_v1.json \
  --sample-size 20 \
  --boundary-cases evaluation/agent_eval_boundary_suite_v1.json \
  --boundary-sample-size 10 \
  --seed 20260222 \
  --out evaluation/agent_eval_report.json \
  --md-out evaluation/agent_eval_report.md
```

## Agent Eval Trend
```bash
python evaluation/run_agent_eval_trend.py \
  --glob "evaluation/agent_eval_report*.json" \
  --out evaluation/agent_eval_trend.json
```
