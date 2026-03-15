#!/bin/bash
# Agent V2 快速诊断脚本
# Usage: ./scripts/diagnose-agent.sh

echo "================================"
echo "Agent V2 诊断工具"
echo "================================"
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "1. 检查容器状态"
echo "----------------"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep fkv
echo ""

echo "2. 检查模型配置"
echo "----------------"
docker exec fkv-api python -c "
from app.runtime_config import get_runtime_setting
print(f'Router Model: {get_runtime_setting(\"planner_model\", None)}')
print(f'Synthesizer Model: {get_runtime_setting(\"synthesizer_model\", None)}')
print(f'Embed Model: {get_runtime_setting(\"embed_model\", None)}')
" 2>/dev/null || echo -e "${RED}无法获取配置${NC}"
echo ""

echo "3. 检查 Ollama 连接"
echo "-------------------"
OLLAMA_URL=$(docker exec fkv-api python -c "from app.runtime_config import get_runtime_setting; print(get_runtime_setting('ollama_base_url', None))" 2>/dev/null)
echo "Ollama URL: $OLLAMA_URL"
curl -s "$OLLAMA_URL/api/tags" | jq -r '.models | length' 2>/dev/null | xargs -I {} echo "可用模型: {}" || echo -e "${RED}Ollama 连接失败${NC}"
echo ""

echo "4. 检查 Qdrant 连接"
echo "-------------------"
docker exec fkv-api python -c "
import requests
try:
    resp = requests.get('http://qdrant:6333/collections/fkv_docs_v1', timeout=5)
    print(f'Qdrant 状态: {resp.status_code}')
    data = resp.json()
    print(f'向量数: {data.get(\"result\", {}).get(\"vectors_count\", \"N/A\")}')
except Exception as e:
    print(f'连接失败: {e}')
" 2>/dev/null

echo ""
echo "5. 最近错误日志"
echo "---------------"
docker logs fkv-api --since 1h 2>&1 | grep -i "error\|exception\|failed" | tail -10 || echo "无错误日志"
echo ""

echo "6. Agent V2 A/B 测试报告"
echo "-------------------------"
curl -s -H "Cookie: fkv_token=test" \
  http://localhost:18180/v1/agent/ab-test-report 2>/dev/null | jq '.' || echo -e "${YELLOW}需要登录后才能查看${NC}"
echo ""

echo "7. 测试简单查询"
echo "--------------"
echo "发送测试请求: '你好'"
curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "Cookie: fkv_token=test" \
  -d '{"query": "你好", "ui_lang": "zh"}' \
  http://localhost:18180/v1/agent/execute 2>/dev/null | jq -r '.card.short_summary.zh' || echo -e "${RED}请求失败${NC}"
echo ""

echo "================================"
echo "诊断完成"
echo "================================"
