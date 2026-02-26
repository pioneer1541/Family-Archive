#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

import requests


def run() -> int:
    ap = argparse.ArgumentParser(description='Planner/fallback baseline evaluation')
    ap.add_argument('--api', default='http://127.0.0.1:18180')
    ap.add_argument('--cases', default='evaluation/planner_fallback_cases.json')
    ap.add_argument('--out', default='evaluation/planner_eval_report.json')
    args = ap.parse_args()

    cases_obj = json.loads(Path(args.cases).read_text(encoding='utf-8'))
    cases = cases_obj.get('cases') if isinstance(cases_obj, dict) else None
    if not isinstance(cases, list) or not cases:
        raise SystemExit('invalid_cases_file')

    rows = []
    intent_ok = 0
    fallback_ok = 0

    for case in cases:
        query = str(case.get('query') or '').strip()
        expected_intent = str(case.get('expected_intent') or '').strip()
        expect_fallback = bool(case.get('expect_fallback'))

        t0 = time.time()
        r = requests.post(
            args.api.rstrip('/') + '/v1/agent/plan',
            json={'query': query, 'ui_lang': 'zh', 'query_lang': 'auto', 'doc_scope': {}},
            timeout=20,
        )
        latency_ms = int((time.time() - t0) * 1000)
        r.raise_for_status()
        out = r.json()

        got_intent = str(out.get('intent') or '')
        got_fallback = str(out.get('fallback') or '')
        conf = float(out.get('confidence') or 0.0)
        got_is_fallback = conf < 0.55

        i_ok = got_intent == expected_intent
        f_ok = got_is_fallback == expect_fallback
        if i_ok:
            intent_ok += 1
        if f_ok:
            fallback_ok += 1

        rows.append(
            {
                'query': query,
                'expected_intent': expected_intent,
                'got_intent': got_intent,
                'confidence': conf,
                'expect_fallback': expect_fallback,
                'got_fallback': got_fallback,
                'fallback_triggered': got_is_fallback,
                'intent_ok': i_ok,
                'fallback_ok': f_ok,
                'latency_ms': latency_ms,
            }
        )

    total = len(rows)
    intent_acc = (intent_ok / total) if total else 0.0
    fallback_acc = (fallback_ok / total) if total else 0.0

    report = {
        'ok': True,
        'api': args.api,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'summary': {
            'total': total,
            'intent_acc': round(intent_acc, 4),
            'fallback_acc': round(fallback_acc, 4),
            'intent_target_95_pass': intent_acc >= 0.95,
            'fallback_target_95_pass': fallback_acc >= 0.95,
        },
        'rows': rows,
    }

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('planner_eval')
    print('out:', args.out)
    print('total:', total, 'intent_acc:', round(intent_acc, 4), 'fallback_acc:', round(fallback_acc, 4))
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
