# Agent V2 Phase 2: 单LLM模式 - 已实现

## 概述

Phase 2 将 Agent V2 的 **2次LLM调用** (Router + Synthesizer) 优化为 **1次LLM调用**（简单查询），在保持准确率的前提下降低成本和延迟。

---

## 实现架构

### Graph 流程

```
START → QueryClassifier
           ↓
    ┌──────┴──────┐
  simple        complex
    ↓              ↓
unified      router → retrieve → synthesizer
    ↓              ↓
   END            END
```

| 路径 | LLM调用 | 适用场景 |
|------|---------|----------|
| **simple** (unified) | 1次 | 问候、简单查询、单事实检索 |
| **complex** (router→synthesize) | 2次 | 计算、分析、多步推理 |

---

## 核心组件

### 1. QueryClassifier (`nodes/query_classifier.py`)

**功能**：判定查询复杂度，决定走单LLM还是双LLM路径

**判定策略**：
1. **规则快速通道**：关键词匹配（零成本）
2. **LLM分类器**：规则不确定时使用轻量模型
3. **A/B测试覆盖**：灰度发布时强制分配模式

**输出**：
```python
{
    "complexity": "simple" | "complex",
    "confidence": 0.0-1.0,
    "method": "rule" | "llm" | "ab_test"
}
```

### 2. UnifiedSynthesizer (`nodes/unified_synthesizer.py`)

**功能**：单次LLM调用完成路由+答案生成

**流程**：
1. 构建融合Prompt（查询+上下文）
2. 单次LLM调用
3. 解析输出（intent + answer）
4. 失败时返回fallback响应

### 3. A/B测试框架 (`ab_test_metrics.py`)

**功能**：对比单LLM vs 双LLM模式的性能指标

**指标收集**：
- LLM调用次数
- 响应延迟
- 成功率
- 成本节省估算

**API**：
```
GET /v1/agent/ab-test-report
```

---

## 配置

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AGENT_V2_ENABLED` | Agent V2 总开关 | `false` |
| `AGENT_V2_SINGLE_LLM_ENABLED` | 单LLM模式开关 | `true` |
| `AGENT_V2_SINGLE_LLM_TRAFFIC_PERCENT` | 单LLM流量比例 (0-100) | `50` |

### 使用示例

**全量单LLM模式**：
```bash
AGENT_V2_SINGLE_LLM_ENABLED=true
AGENT_V2_SINGLE_LLM_TRAFFIC_PERCENT=100
```

**50% A/B测试**：
```bash
AGENT_V2_SINGLE_LLM_TRAFFIC_PERCENT=50
```

**回滚双LLM**：
```bash
AGENT_V2_SINGLE_LLM_TRAFFIC_PERCENT=0
```

---

## 文件结构

```
backend/app/services/agent_v2/
├── nodes/
│   ├── query_classifier.py      # 查询复杂度分类器
│   ├── unified_synthesizer.py   # 单LLM融合节点
│   ├── router.py               # 原路由节点
│   ├── retriever.py            # 检索节点
│   ├── synthesizer.py          # 合成节点
│   └── ...
├── ab_test_metrics.py           # A/B测试指标收集
├── config.py                    # 配置（含单LLM模式）
├── graph.py                     # Graph定义（含条件路由）
└── state.py                     # 状态定义（含classifier字段）
```

---

## 测试

### 单元测试

```bash
cd backend
pytest app/services/agent_v2/tests/test_phase2_comparison.py -v
```

### A/B测试报告

```bash
# 查看对比指标
curl http://localhost:18180/v1/agent/ab-test-report \
  -H "Cookie: fkv_token=$TOKEN"
```

返回示例：
```json
{
  "ok": true,
  "report": {
    "single_llm": {
      "count": 150,
      "avg_duration_ms": 1250.5,
      "success_rate": 98.5,
      "avg_llm_calls": 1.0
    },
    "dual_llm": {
      "count": 150,
      "avg_duration_ms": 2800.3,
      "success_rate": 99.0,
      "avg_llm_calls": 2.0
    },
    "cost_saving_estimate": {
      "percent": 25.0,
      "single_ratio": 50.0
    }
  }
}
```

---

## 预期收益

| 指标 | Phase 1 | Phase 2 | 提升 |
|------|---------|---------|------|
| 平均LLM调用/查询 | 2.0次 | 1.3次 | -35% |
| 简单查询延迟 | ~3s | ~1.5s | -50% |
| 云端模型成本 | 100% | 65% | -35% |

---

## 后续计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | LangGraph 重构 | ✅ 已完成 |
| Phase 2 | 单LLM模式 | ✅ 已完成 |
| Phase 3 | P1 快速优化 | ⏳ 计划中 |
| Phase 4 | 流式输出 | ⏳ 计划中 |

---

*最后更新: 2026-03-15*
