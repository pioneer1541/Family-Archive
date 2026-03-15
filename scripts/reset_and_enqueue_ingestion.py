#!/usr/bin/env python3
import json
import os
import sys
import uuid
from datetime import datetime

sys.path.insert(0, '/app')

from app.db import SessionLocal
from app.models import IngestionJob
from app.services.ingestion import enqueue_ingestion_job

BASE_PATH = '/volume1/Family_Archives/mail_attachments/2026/02'
BATCH_SIZE = 5


def main():
    db = SessionLocal()

    running = db.query(IngestionJob).filter_by(status='running').all()
    for job in running:
        job.status = 'failed'
        job.error_code = 'stale_running_job'
        job.finished_at = datetime.utcnow()
    db.commit()
    print(f'reset_running_jobs={len(running)}')

    files = []
    for root, _, filenames in os.walk(BASE_PATH):
        for f in filenames:
            if f.lower().endswith('.pdf'):
                files.append(os.path.join(root, f))
    files.sort()
    print(f'found_files={len(files)}')

    if not files:
        print('no_files_found')
        return

    batch = files[:BATCH_SIZE]
    job = IngestionJob(
        id=str(uuid.uuid4()),
        input_paths=json.dumps(batch),
        status='pending',
        success_count=0,
        failed_count=0,
        duplicate_count=0,
    )
    db.add(job)
    db.commit()
    print(f'created_job={job.id}')
    print(f'batch_size={len(batch)}')

    mode = enqueue_ingestion_job(job.id)
    print(f'enqueue_mode={mode}')


if __name__ == '__main__':
    main()
