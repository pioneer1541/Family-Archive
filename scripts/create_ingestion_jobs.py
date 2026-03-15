#!/usr/bin/env python3
import json
import uuid
import os
import sys
sys.path.insert(0, '/app')

from app.db import SessionLocal
from app.models import IngestionJob

db = SessionLocal()

base_path = '/volume1/Family_Archives/mail_attachments/2026/02'
files = []

for root, dirs, filenames in os.walk(base_path):
    for f in filenames:
        if f.endswith('.pdf'):
            files.append(os.path.join(root, f))

print(f'Found {len(files)} PDF files')

if files:
    job = IngestionJob(
        id=str(uuid.uuid4()),
        input_paths=json.dumps(files[:10]),  # 先处理前10个
        status='pending',
        success_count=0,
        failed_count=0,
        duplicate_count=0
    )
    db.add(job)
    db.commit()
    print(f'Created job: {job.id}')
    print(f'Files: {len(files[:10])}')
