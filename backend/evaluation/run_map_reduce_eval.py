#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

import requests


def run() -> int:
    ap = argparse.ArgumentParser(description='Map-reduce summary baseline evaluation')
    ap.add_argument('--api', default='http://127.0.0.1:18180')
    ap.add_argument('--min-chunks', type=int, default=1)
    ap.add_argument('--out', default='evaluation/map_reduce_eval_report.json')
    args = ap.parse_args()

    api = args.api.rstrip('/')
    q = requests.get(api + '/v1/queue', timeout=20)
    q.raise_for_status()
    queue = q.json()
    docs = queue.get('documents') if isinstance(queue, dict) else []
    if not isinstance(docs, list) or not docs:
        raise SystemExit('no_documents_available')
    docs = [d for d in docs if str(d.get('status') or '') == 'completed']
    if not docs:
        raise SystemExit('no_completed_documents_available')

    rows = []
    ok = 0

    for doc in docs[:10]:
        doc_id = str(doc.get('doc_id') or '').strip()
        if not doc_id:
            continue

        t0 = time.time()
        r = requests.post(api + '/v1/summaries/map-reduce', json={'doc_id': doc_id, 'ui_lang': 'zh', 'chunk_group_size': 6}, timeout=25)
        latency_ms = int((time.time() - t0) * 1000)
        if r.status_code >= 400:
            rows.append({'doc_id': doc_id, 'ok': False, 'status_code': r.status_code, 'latency_ms': latency_ms})
            continue

        out = r.json()
        total_chunks = int(out.get('total_chunks') or 0)
        sections = out.get('sections') if isinstance(out.get('sections'), list) else []
        sources = out.get('sources') if isinstance(out.get('sources'), list) else []
        short = out.get('short_summary') if isinstance(out.get('short_summary'), dict) else {}
        has_bilingual = bool(short.get('en')) and bool(short.get('zh'))
        section_bilingual = True
        min_section_len_ok = True
        section_chars = []
        for sec in sections:
            summary = sec.get('summary') if isinstance(sec, dict) else {}
            en_txt = str(summary.get('en') or '').strip() if isinstance(summary, dict) else ''
            zh_txt = str(summary.get('zh') or '').strip() if isinstance(summary, dict) else ''
            if not en_txt or not zh_txt:
                section_bilingual = False
            if len(en_txt) < 24 or len(zh_txt) < 12:
                min_section_len_ok = False
            section_chars.append(len(en_txt))

        avg_section_chars = int(sum(section_chars) / len(section_chars)) if section_chars else 0
        traceable_sources = len(sources) >= min(3, max(1, len(sections)))

        passed = (
            total_chunks >= int(args.min_chunks)
            and len(sections) > 0
            and len(sources) > 0
            and has_bilingual
            and section_bilingual
            and min_section_len_ok
            and traceable_sources
        )
        if passed:
            ok += 1

        rows.append(
            {
                'doc_id': doc_id,
                'ok': passed,
                'total_chunks': total_chunks,
                'sections': len(sections),
                'sources': len(sources),
                'has_bilingual_short_summary': has_bilingual,
                'section_bilingual': section_bilingual,
                'min_section_len_ok': min_section_len_ok,
                'avg_section_chars': avg_section_chars,
                'traceable_sources': traceable_sources,
                'latency_ms': latency_ms,
            }
        )

    total = len(rows)
    pass_rate = (ok / total) if total else 0.0
    report = {
        'ok': True,
        'api': args.api,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'summary': {
            'total': total,
            'pass': ok,
            'pass_rate': round(pass_rate, 4),
            'target_85_pass': pass_rate >= 0.85,
        },
        'rows': rows,
    }

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('map_reduce_eval')
    print('out:', args.out)
    print('total:', total, 'pass:', ok, 'pass_rate:', round(pass_rate, 4))
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
