#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

import requests


def _poll_job(session: requests.Session, api: str, job_id: str, timeout_sec: int = 180) -> dict:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        job = session.get(f'{api}/v1/ingestion/jobs/{job_id}', timeout=20).json()
        if str(job.get('status')) in ('completed', 'failed'):
            return job
        time.sleep(1)
    raise TimeoutError(f'job_timeout:{job_id}')


def run() -> int:
    ap = argparse.ArgumentParser(description='M1 regression runner for ingestion/search queue baseline')
    ap.add_argument('--api', default='http://127.0.0.1:18180')
    ap.add_argument('--sample-dir', default='/home/pioneer1541/ai-stack/family-vault/backend/ingest_samples')
    ap.add_argument('--container-prefix', default='/app/ingest_samples')
    ap.add_argument('--out', default='evaluation/m1_regression_report.json')
    args = ap.parse_args()

    host_dir = Path(args.sample_dir)
    files = sorted([p for p in host_dir.iterdir() if p.is_file()])
    if not files:
        raise SystemExit('no_sample_files')

    file_paths = [f"{args.container_prefix.rstrip('/')}/{p.name}" for p in files]
    session = requests.Session()
    api = args.api.rstrip('/')

    created_at = time.strftime('%Y-%m-%dT%H:%M:%S%z')

    # primary run
    primary = session.post(f'{api}/v1/ingestion/jobs', json={'file_paths': file_paths}, timeout=20).json()
    primary_job = _poll_job(session, api, primary['job_id'])

    # dedup run
    dedup = session.post(f'{api}/v1/ingestion/jobs', json={'file_paths': file_paths}, timeout=20).json()
    dedup_job = _poll_job(session, api, dedup['job_id'])

    # search checks
    search_queries = ['electricity', '家庭知识库', 'milestone', 'Queue']
    search_rows = []
    for q in search_queries:
        s = session.post(
            f'{api}/v1/search',
            json={'query': q, 'top_k': 5, 'score_threshold': 0, 'ui_lang': 'zh', 'query_lang': 'auto'},
            timeout=20,
        ).json()
        hits = s.get('hits') if isinstance(s, dict) else []
        hit_count = len(hits) if isinstance(hits, list) else 0
        search_rows.append({'query': q, 'hit_count': hit_count, 'ok': hit_count > 0})

    queue = session.get(f'{api}/v1/queue', timeout=20).json()
    docs = queue.get('documents') if isinstance(queue, dict) else []
    completed = [d for d in docs if str(d.get('status') or '') == 'completed'] if isinstance(docs, list) else []

    reprocess = {'ok': False}
    if completed:
        doc_id = str(completed[0]['doc_id'])
        rep = session.post(f'{api}/v1/documents/{doc_id}/reprocess', timeout=20).json()
        rep_job = _poll_job(session, api, rep['job_id'])
        reprocess = {'ok': rep_job.get('status') == 'completed', 'doc_id': doc_id, 'job': rep_job}

    total = len(file_paths)
    success_count = int(primary_job.get('success_count') or 0)
    failed_count = int(primary_job.get('failed_count') or 0)
    duplicate_count = int(primary_job.get('duplicate_count') or 0)
    pass_rate = (success_count / total) if total else 0.0

    summary = {
        'total_files': total,
        'success_count': success_count,
        'failed_count': failed_count,
        'duplicate_count': duplicate_count,
        'pass_rate': round(pass_rate, 4),
        'threshold_97_pass': pass_rate >= 0.97,
        'dedup_all_detected': int(dedup_job.get('duplicate_count') or 0) >= total,
        'search_all_nonzero': all(row['ok'] for row in search_rows),
        'reprocess_pass': bool(reprocess.get('ok')),
    }

    report = {
        'ok': True,
        'generated_at': created_at,
        'api': api,
        'samples': file_paths,
        'summary': summary,
        'primary_job': primary_job,
        'dedup_job': dedup_job,
        'search_checks': search_rows,
        'queue_totals': queue.get('totals') if isinstance(queue, dict) else {},
        'reprocess': reprocess,
    }

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('m1_regression')
    print('out:', args.out)
    print('summary:', json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
