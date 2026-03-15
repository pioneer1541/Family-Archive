#!/bin/bash
# 重新索引所有文档脚本

echo "================================"
echo "文档重新索引工具"
echo "================================"
echo ""

# 1. 清空 Qdrant（保持 768 维配置）
echo "1. 清空 Qdrant 向量"
curl -X POST http://localhost:16333/collections/fkv_docs_v1/points/delete \
  -H "Content-Type: application/json" \
  -d '{"filter": {}}' 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin))'

echo ""
echo "2. 为每个文档创建新的 ingestion job"

# 获取所有文档并创建 jobs
docker exec fkv-api python3 << 'PYEOF'
import json
import uuid
from app.db import SessionLocal
from app.models import Document, IngestionJob

db = SessionLocal()
docs = db.query(Document).all()

print(f"Found {len(docs)} documents")

count = 0
for doc in docs:
    if doc.source_path:
        job = IngestionJob(
            id=str(uuid.uuid4()),
            input_paths=json.dumps([doc.source_path]),
            status='pending',
            success_count=0,
            failed_count=0,
            duplicate_count=0
        )
        db.add(job)
        count += 1

db.commit()
print(f"Created {count} ingestion jobs")
PYEOF

echo ""
echo "3. 触发处理（可能需要几分钟）"
docker exec fkv-worker celery -A app.worker control rate_limit fkv.ingestion.process_job 10/m 2>/dev/null || true

echo ""
echo "4. 监控进度"
echo "运行以下命令监控:"
echo "  watch -n 5 'curl -s http://localhost:16333/collections/fkv_docs_v1 | python3 -c \"import json,sys; d=json.load(sys.stdin); print(\"向量数:\", d.get(\"result\",{}).get(\"vectors_count\",0))\"'"

echo ""
echo "================================"
echo "重新索引已启动"
echo "================================"
