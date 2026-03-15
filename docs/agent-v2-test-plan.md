# Agent V2 全面测试计划与评估指标

## 1. 测试目标

- 量化评估 Agent V2 对话质量
- 识别性能瓶颈和失败模式
- 建立持续改进的基准线
- 验证 Phase 2/3/4 优化效果

---

## 2. 测试分类

### 2.1 功能测试

| 测试项 | 测试内容 | 通过标准 |
|--------|----------|----------|
| Chitchat 响应 | "你好", "谢谢", "再见" | 0 LLM 调用，响应 <100ms |
| 简单查询 | "我的护照在哪里" | 1 LLM 调用，准确返回答案 |
| 复杂查询 | "分析过去一年的支出" | 2 LLM 调用，返回完整分析 |
| 多轮对话 | 上下文跟随 | 正确引用前文 |
| 边界情况 | 空查询/超长查询 | 优雅降级，不崩溃 |

### 2.2 性能测试

| 指标 | 目标值 | 测量方法 |
|------|--------|----------|
| 首字节时间 (TTFB) | <100ms | 从请求到第一个事件 |
| Chitchat 延迟 | <200ms | 端到端响应时间 |
| 简单查询延迟 | <1.5s | 端到端响应时间 |
| 复杂查询延迟 | <5s | 端到端响应时间 |
| LLM 调用次数 | 平均 <1.5 | 每次查询统计 |
| 错误率 | <1% | 失败请求比例 |

### 2.3 质量评估

#### 2.3.1 响应相关性 (Relevance)
```
评分标准:
- 5分: 完全回答用户问题
- 4分: 基本回答，有小遗漏
- 3分: 部分回答
- 2分: 与问题相关但未回答
- 1分: 完全不相关
```

#### 2.3.2 信息准确性 (Accuracy)
```
评分标准:
- 5分: 信息完全准确，有据可查
- 4分: 信息基本准确
- 3分: 信息部分准确
- 2分: 信息有误导
- 1分: 信息错误
```

#### 2.3.3 响应完整性 (Completeness)
```
评分标准:
- 5分: 提供了所有相关信息
- 4分: 提供了主要信息
- 3分: 信息有所缺失
- 2分: 信息严重缺失
- 1分: 未提供有用信息
```

#### 2.3.4 语言表达 (Fluency)
```
评分标准:
- 5分: 表达自然流畅
- 4分: 表达基本流畅
- 3分: 表达有些生硬
- 2分: 表达不连贯
- 1分: 难以理解
```

---

## 3. 测试数据集

### 3.1 测试查询设计

```python
TEST_QUERIES = {
    "chitchat": [
        {"query": "你好", "expected": "greeting"},
        {"query": "谢谢", "expected": "thanks_response"},
        {"query": "再见", "expected": "goodbye"},
        {"query": "hello", "expected": "greeting"},
    ],
    
    "simple_lookup": [
        {"query": "我的护照在哪里", "expected_doc_types": ["passport"]},
        {"query": "保险什么时候到期", "expected_doc_types": ["insurance"]},
        {"query": "账单金额是多少", "expected_doc_types": ["bill"]},
    ],
    
    "complex_analysis": [
        {"query": "计算过去一年的平均支出", "requires_calculation": True},
        {"query": "比较两份保险的覆盖范围", "requires_comparison": True},
        {"query": "提取合同的关键条款", "requires_extraction": True},
    ],
    
    "multi_turn": [
        {
            "turns": [
                {"query": "我的保险信息", "expected": "insurance_info"},
                {"query": "具体保障哪些项目", "expected": "coverage_details"},
                {"query": "续费时间呢", "expected": "renewal_date"},
            ]
        }
    ],
    
    "edge_cases": [
        {"query": "", "expected_behavior": "error_handling"},
        {"query": "a" * 10000, "expected_behavior": "truncate_or_reject"},
        {"query": "@#$%^&*", "expected_behavior": "graceful_handling"},
    ]
}
```

### 3.2 评估样本数量

| 类别 | 样本数 | 说明 |
|------|--------|------|
| Chitchat | 20 | 常见问候语 |
| 简单查询 | 50 | 单事实检索 |
| 复杂查询 | 30 | 需要推理分析 |
| 多轮对话 | 10 | 3-5轮对话 |
| 边界情况 | 10 | 异常输入 |
| **总计** | **120** | 覆盖主要场景 |

---

## 4. 自动化测试脚本

### 4.1 批量测试脚本

```python
# backend/app/services/agent_v2/tests/test_evaluation.py

import json
import time
from typing import Any
import pytest

from app.schemas import AgentExecuteRequest
from app.services.agent_v2 import execute as execute_v2


class TestAgentV2Evaluation:
    """Comprehensive evaluation test suite."""
    
    # Test data
    CHITCHAT_QUERIES = ["你好", "您好", "谢谢", "再见", "hello", "thanks"]
    SIMPLE_QUERIES = [
        "我的护照在哪里",
        "保险什么时候到期", 
        "账单金额是多少",
        "合同在哪里",
    ]
    COMPLEX_QUERIES = [
        "计算过去一年的平均支出",
        "分析所有账单的趋势",
        "对比两份保险的覆盖范围",
    ]
    
    @pytest.mark.asyncio
    async def test_chitchat_zero_llm(self, mock_db):
        """Verify chitchat uses 0 LLM calls."""
        for query in self.CHITCHAT_QUERIES:
            req = AgentExecuteRequest(query=query, ui_lang="zh")
            
            # Track LLM calls
            llm_calls = []
            
            with patch_llm_calls(llm_calls):
                result = await execute_v2(req, mock_db, None, True)
            
            assert len(llm_calls) == 0, f"{query} should use 0 LLM calls"
            assert result.executor_stats.graph_complexity == "simple"
            assert result.card.short_summary.zh is not None
    
    @pytest.mark.asyncio
    async def test_simple_query_single_llm(self, mock_db):
        """Verify simple queries use 1 LLM call."""
        for query in self.SIMPLE_QUERIES:
            req = AgentExecuteRequest(query=query, ui_lang="zh")
            
            llm_calls = []
            start_time = time.time()
            
            with patch_llm_calls(llm_calls):
                result = await execute_v2(req, mock_db, None, True)
            
            duration = time.time() - start_time
            
            # Performance check
            assert duration < 3.0, f"{query} took {duration:.2f}s, too slow"
            
            # Quality checks
            assert result.card.short_summary.zh, f"{query} returned empty summary"
            assert len(result.card.short_summary.zh) > 5, f"{query} summary too short"
    
    @pytest.mark.asyncio
    async def test_complex_query_quality(self, mock_db):
        """Evaluate complex query response quality."""
        for query in self.COMPLEX_QUERIES:
            req = AgentExecuteRequest(query=query, ui_lang="zh")
            
            result = await execute_v2(req, mock_db, None, True)
            
            # Basic validation
            assert result.card.title, "Missing title"
            assert result.card.short_summary.zh, "Missing summary"
            assert len(result.card.key_points) > 0, "Missing key points"
    
    @pytest.mark.asyncio
    async def test_latency_benchmark(self, mock_db):
        """Benchmark latencies by category."""
        latencies = {"chitchat": [], "simple": [], "complex": []}
        
        # Test chitchat
        for query in self.CHITCHAT_QUERIES[:3]:
            start = time.time()
            req = AgentExecuteRequest(query=query, ui_lang="zh")
            await execute_v2(req, mock_db, None, True)
            latencies["chitchat"].append(time.time() - start)
        
        # Test simple
        for query in self.SIMPLE_QUERIES[:3]:
            start = time.time()
            req = AgentExecuteRequest(query=query, ui_lang="zh")
            await execute_v2(req, mock_db, None, True)
            latencies["simple"].append(time.time() - start)
        
        # Test complex
        for query in self.COMPLEX_QUERIES[:2]:
            start = time.time()
            req = AgentExecuteRequest(query=query, ui_lang="zh")
            await execute_v2(req, mock_db, None, True)
            latencies["complex"].append(time.time() - start)
        
        # Report
        print(f"\nLatency Report:")
        print(f"  Chitchat: {sum(latencies['chitchat'])/len(latencies['chitchat'])*1000:.0f}ms avg")
        print(f"  Simple:   {sum(latencies['simple'])/len(latencies['simple'])*1000:.0f}ms avg")
        print(f"  Complex:  {sum(latencies['complex'])/len(latencies['complex'])*1000:.0f}ms avg")
```

### 4.2 人工评估模板

```python
# scripts/manual_evaluation.py

"""
人工评估数据收集工具

Usage:
    python scripts/manual_evaluation.py --query "测试查询" --output eval_result.json
"""

import json
import argparse
from datetime import datetime


def collect_evaluation(query: str, response: dict) -> dict:
    """Collect manual evaluation scores."""
    
    print(f"\n{'='*50}")
    print(f"Query: {query}")
    print(f"{'='*50}")
    
    print(f"\nResponse:")
    print(f"  Title: {response.get('title', 'N/A')}")
    print(f"  Summary: {response.get('short_summary', {}).get('zh', 'N/A')}")
    print(f"  Key Points: {len(response.get('key_points', []))}")
    
    print(f"\n请评分 (1-5):")
    
    scores = {
        "relevance": int(input("  相关性 (问题是否被回答): ")),
        "accuracy": int(input("  准确性 (信息是否正确): ")),
        "completeness": int(input("  完整性 (信息是否完整): ")),
        "fluency": int(input("  流畅度 (表达是否自然): ")),
    }
    
    comments = input("\n其他意见 (可选): ").strip()
    
    return {
        "query": query,
        "response": response,
        "scores": scores,
        "overall": sum(scores.values()) / len(scores),
        "comments": comments,
        "evaluated_at": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--response", required=True, help="JSON file with response")
    parser.add_argument("--output", default="eval_result.json")
    args = parser.parse_args()
    
    with open(args.response) as f:
        response = json.load(f)
    
    result = collect_evaluation(args.query, response)
    
    with open(args.output, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n评估已保存到 {args.output}")
    print(f"总体评分: {result['overall']:.2f}/5")
```

---

## 5. 评估指标仪表盘

### 5.1 关键指标 (KPIs)

| KPI | 当前目标 | 优秀 | 测量频率 |
|-----|---------|------|----------|
| 平均响应时间 | <2s | <1s | 实时 |
| Chitchat 0 LLM 比例 | >90% | >95% | 每日 |
| 简单查询 1 LLM 比例 | >80% | >90% | 每日 |
| 平均 LLM 调用/查询 | <1.5 | <1.3 | 每日 |
| 用户满意度 | >4.0/5 | >4.5 | 每周 |
| 错误率 | <2% | <1% | 实时 |
| 缓存命中率 | >30% | >50% | 每日 |

### 5.2 A/B 测试指标

```python
# 通过 /v1/agent/ab-test-report 获取

AB_TEST_METRICS = {
    "single_llm": {
        "count": "单LLM模式调用次数",
        "avg_duration_ms": "平均响应时间",
        "success_rate": "成功率",
    },
    "dual_llm": {
        "count": "双LLM模式调用次数", 
        "avg_duration_ms": "平均响应时间",
        "success_rate": "成功率",
    },
    "cost_saving": {
        "percent": "成本节省百分比",
        "single_ratio": "单LLM模式占比",
    }
}
```

---

## 6. 测试执行计划

### 6.1 每日自动化测试

```yaml
# .github/workflows/agent-evaluation.yml
name: Agent V2 Daily Evaluation

on:
  schedule:
    - cron: '0 2 * * *'  # 每天凌晨 2 点
  workflow_dispatch:

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Run evaluation tests
        run: |
          cd backend
          pytest app/services/agent_v2/tests/test_evaluation.py -v --tb=short
      
      - name: Generate report
        run: |
          python scripts/generate_eval_report.py --output eval_report.json
      
      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: evaluation-report
          path: eval_report.json
```

### 6.2 每周人工评估

- 抽样 20 条真实用户查询
- 3 人独立评分
- 计算平均分和分歧度
- 更新质量基准

### 6.3 每月全面评估

- 全量测试集 (120 条)
- 端到端性能测试
- A/B 测试效果分析
- 生成改进建议报告

---

## 7. 问题诊断流程

### 7.1 效果差的排查清单

```
1. 检查模型配置
   - Router 模型是否正确
   - Synthesizer 模型是否正确
   - 模型是否可访问

2. 检查检索质量
   - Qdrant 连接是否正常
   - 向量维度是否匹配
   - 检索结果是否相关

3. 检查分类器
   - QueryClassifier 是否正确分类
   - 简单查询是否走 unified
   - Chitchat 是否短路

4. 检查流式输出
   - 事件是否正常发送
   - 前端是否正确解析
   - 是否有延迟或卡顿

5. 检查日志
   - 查看 error 级别日志
   - 检查 LLM 调用超时
   - 检查内存/CPU 使用
```

### 7.2 快速调试命令

```bash
# 检查 Agent V2 状态
curl -H "Cookie: fkv_token=$TOKEN" \
  https://vault.thisvshome.com/v1/agent/ab-test-report

# 测试简单查询
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Cookie: fkv_token=$TOKEN" \
  -d '{"query": "你好", "locale": "zh-CN"}' \
  https://vault.thisvshome.com/v1/agent/execute

# 查看后端日志
docker logs fkv-api --tail 100 | grep -i "agent\|classifier\|router"

# 查看 Worker 日志
docker logs fkv-worker --tail 100 | grep -i "agent\|llm"
```

---

## 8. 改进路线图

### 8.1 短期 (1-2 周)

- [ ] 完成全面测试数据集
- [ ] 建立自动化评估流水线
- [ ] 修复已识别的关键问题

### 8.2 中期 (1 个月)

- [ ] 优化检索质量
- [ ] 改进分类器准确度
- [ ] 完善流式输出体验

### 8.3 长期 (3 个月)

- [ ] 引入 RLHF 微调
- [ ] 支持多模态输入
- [ ] 个性化记忆系统

---

*测试计划 v1.0 - 2026-03-15*
