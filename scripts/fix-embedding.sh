#!/bin/bash
# 文档嵌入修复脚本
# 解决 Qdrant 向量为 0 的问题

set -e

echo "================================"
echo "文档嵌入修复工具"
echo "================================"
echo ""

cd ~/ai-stack/family-vault

echo "1. 检查当前状态"
echo "---------------"
VECTOR_COUNT=$(curl -s http://localhost:16333/collections/fkv_docs_v1 | python3 -c 'import json,sys; print(json.load(sys.stdin).get("result",{}).get("vectors_count",0))')
echo "当前向量数: $VECTOR_COUNT"

echo ""
echo "2. 重启 Worker 确保任务注册"
echo "----------------------------"
docker compose restart fkv-worker
sleep 5

echo ""
echo "3. 检查 Celery 注册的任务"
echo "-------------------------"
docker exec fkv-worker celery -A app.worker inspect registered 2>&1 | head -20 || echo "无法列出任务"

echo ""
echo "4. 触发文档扫描"
echo "---------------"
# 通过 API 触发扫描 (需要先登录获取 token)
echo "请在前端登录后，访问:"
echo "  https://vault.thisvshome.com/admin/ingestion"
echo ""
echo "或者手动触发扫描:"
echo "  1. 进入前端"
echo "  2. 导航到 文档管理 → 扫描目录"
echo "  3. 点击扫描"

echo ""
echo "5. 监控嵌入进度"
echo "---------------"
echo "运行以下命令监控:"
echo "  watch -n 5 'curl -s http://localhost:16333/collections/fkv_docs_v1 | python3 -c \"import json,sys; print(\"向量数:\", json.load(sys.stdin).get(\"result\",{}).get(\"vectors_count\",0))\"'"

echo ""
echo "================================"
echo "修复步骤完成"
echo "================================"
echo ""
echo "如果向量数仍然为 0，请检查:"
echo "  1. Worker 日志: docker logs fkv-worker --tail 50"
echo "  2. API 日志: docker logs fkv-api --tail 50"
echo "  3. Ollama 状态: curl http://192.168.1.162:11434/api/tags"
