#!/usr/bin/env python3
"""
直接创建 ingestion job 处理指定路径的文件。
不依赖 NAS 扫描，直接指定文件路径入库。
"""
import json
import os
import sys
from pathlib import Path

# 添加到 Python 路径
sys.path.insert(0, '/app')

from app.db import SessionLocal
from app import crud
from app.services.ingestion import enqueue_ingestion_job

# NAS 实际路径（通过 Docker volume 挂载到容器内的路径）
NAS_MOUNT_PATH = '/volume1/Family_Archives'

def create_job_for_paths(file_paths: list[str]) -> str:
    """为指定文件路径创建 ingestion job 并入队。"""
    db = SessionLocal()
    try:
        # 过滤存在的文件
        valid_paths = [p for p in file_paths if os.path.isfile(p)]
        if not valid_paths:
            print(f'No valid files found. Checked {len(file_paths)} paths.')
            return None
        
        # 创建 job
        job = crud.create_ingestion_job(db, valid_paths)
        print(f'Created job: {job.id}')
        print(f'Files: {len(valid_paths)}')
        for p in valid_paths[:5]:
            print(f'  - {p}')
        if len(valid_paths) > 5:
            print(f'  ... and {len(valid_paths) - 5} more')
        
        # 入队
        mode = enqueue_ingestion_job(job.id)
        print(f'Enqueue mode: {mode}')
        
        return job.id
    finally:
        db.close()

def scan_and_queue_directory(directory: str, max_files: int = 100) -> str:
    """扫描目录下的文件并创建 ingestion job。"""
    if not os.path.isdir(directory):
        print(f'Directory not found: {directory}')
        return None
    
    # 收集文件
    files = []
    for root, _, filenames in os.walk(directory):
        for f in filenames:
            ext = os.path.splitext(f)[1].lower().lstrip('.')
            if ext in ['pdf', 'docx', 'txt', 'md', 'xlsx', 'jpg', 'jpeg', 'png', 'webp']:
                files.append(os.path.join(root, f))
        if len(files) >= max_files:
            break
    
    if not files:
        print(f'No supported files found in {directory}')
        return None
    
    return create_job_for_paths(files[:max_files])

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Create ingestion job for files')
    parser.add_argument('paths', nargs='*', help='File or directory paths')
    parser.add_argument('--max-files', type=int, default=100, help='Max files to process')
    args = parser.parse_args()
    
    if args.paths:
        # 处理传入的路径
        all_files = []
        for path in args.paths:
            if os.path.isfile(path):
                all_files.append(path)
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for f in files:
                        all_files.append(os.path.join(root, f))
        create_job_for_paths(all_files[:args.max_files])
    else:
        # 默认扫描 NAS 挂载路径
        print(f'Scanning {NAS_MOUNT_PATH}...')
        scan_and_queue_directory(NAS_MOUNT_PATH, max_files=args.max_files)
