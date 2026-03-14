# Agent V2 Phase 2: 单LLM模式设计文档

## 目标

将当前 **2次LLM调用** (Router + Synthesizer) 优化为 **1次LLM调用**，在保持准确率的前提下降低成本50%。

---

## 现状分析

### 当前流程 (2次调用)

```
用户Query → Router LLM (调用#1) → 检索 → Synthesizer LLM (调用#2) → 响应
```

| 节点 | LLM调用 | 职责 |
|------|---------|------|
| Router | ✅ | 意图分类、路由决策、query改写 |
| Retriever | ❌ | 向量检索（无LLM） |
| Synthesizer | ✅ | 答案生成、格式化 |

### 问题

- **强制2次调用**：即使简单查询也要调两次
- **延迟累积**：两次串行调用，延迟相加
- **成本翻倍**：云端模型按调用计费

---

## 方案设计

### 核心思路：智能路由融合

不是所有查询都需要分两次。根据查询类型决定调用策略：

| 查询类型 | 策略 | LLM调用次数 |
|----------|------|-------------|
| **简单查询** (lookup, chitchat) | 单次融合调用 | 1次 |
| **复杂查询** (calculate, detail_extract) | 保持双次调用 | 2次 |
| **系统操作** (system) | 规则处理 | 0次 |

### 新节点：Unified Node

新增 `unified_node` 替代 `router_node → synthesizer_node` 链：

```python
async def unified_node(state, config):
    """
    单LLM模式核心节点：
    1. 先判断查询复杂度
    2. 简单查询：1次调用完成路由+答案
    3. 复杂查询：降级到原流程（Router→Retriever→Synthesizer）
    """
```

### Prompt 设计

**融合Prompt结构**：

```
[系统指令]
你是一个智能助手，需要同时完成两个任务：
1. 理解用户意图
2. 基于检索到的文档内容生成答案

[检索上下文]
{context_chunks}

[用户查询]
{query}

[输出格式]
{
    "intent": "lookup|chitchat|calculate|detail_extract|system",
    "confidence": 0.0-1.0,
    "requires_complex_processing": true|false,
    "answer": {
        "title": "...",
        "short_summary": {"en": "...", "zh": "..."},
        "key_points": [...],
        "sources": [...]
    },
    "fallback_reason": "..."  // 如需要复杂处理，说明原因
}
```

---

## Graph结构变更

### 当前流程

```
START → Router → (Chitchat|Retrieve) → Synthesize → END
```

### Phase 2 流程

```
START → QueryClassifier
         ↓
    ┌────┴────┐
 简单查询    复杂查询
    ↓          ↓
Unified    Router → Retrieve → Synthesize
    ↓
   END
```

### 代码结构

```
backend/app/services/agent_v2/
├── nodes/
│   ├── __init__.py
│   ├── query_classifier.py      # 新增：查询复杂度分类器
│   ├── unified_synthesizer.py   # 新增：单LLM融合节点
│   ├── router.py               # 保留：复杂查询路由
│   ├── retriever.py            # 保留
│   ├── synthesizer.py          # 保留
│   ├── chitchat.py             # 保留
│   └── recovery.py             # 保留
```

---

## 复杂度判定规则

### 简单查询特征 (走单LLM)

| 特征 | 示例 |
|------|------|
| 纯问候/礼貌用语 | "你好", "谢谢" |
| 单一事实查询 | "我的护照在哪", "合同什么时候到期" |
| 直接检索即可回答 | "告诉我关于XX的信息" |
| 无需计算/推理 | "列出所有发票" |

### 复杂查询特征 (走双LLM)

| 特征 | 示例 |
|------|------|
| 需要多步计算 | "计算过去一年的平均支出" |
| 需要详细提取 | "提取这份合同的所有关键条款" |
| 需要对比分析 | "对比这两份保险的覆盖范围" |
| 需要推理判断 | "这份合同有风险吗" |

### 实现方式

**混合策略**：
1. **规则预检**（低成本）：关键词匹配快速识别
2. **轻量LLM分类器**：规则不确定时，用轻量模型判断

```python
async def classify_query_complexity(query: str) -> str:
    """
    返回: "simple" | "complex" | "uncertain"
    """
    # 1. 规则快速通道
    if is_simple_by_rules(query):
        return "simple"
    if is_complex_by_rules(query):
        return "complex"
    
    # 2. LLM轻量分类器
    return await llm_classify(query)
```

---

## Fallback 机制

当单LLM调用失败时，自动降级到双调用模式：

```python
try:
    result = await unified_call(query, context)
    if result.get("fallback_to_dual"):
        # 模型判断需要复杂处理
        return await dual_call_flow(query)
except Exception:
    # 调用失败，降级
    return await dual_call_flow(query)
```

---

## 配置项

```python
# backend/app/services/agent_v2/config.py

class AgentV2Config:
    # 单LLM模式开关
    SINGLE_LLM_MODE: bool = True
    
    # 简单查询判定阈值
    SIMPLE_QUERY_CONFIDENCE_THRESHOLD: float = 0.8
    
    # 降级策略
    AUTO_FALLBACK_TO_DUAL: bool = True
    
    # 模型选择
    UNIFIED_MODEL: str = "glm-4-flash"  # 单LLM用轻量模型
    COMPLEX_ROUTER_MODEL: str = "glm-4-flash"
    COMPLEX_SYNTHESIZER_MODEL: str = "glm-4-plus"  # 复杂查询用强模型
```

---

## 测试策略

### 对比测试

保持与原系统的输出对比：

```python
async def test_single_vs_dual():
    """验证单LLM模式输出与双LLM模式一致"""
    test_queries = load_test_queries()
    
    for query in test_queries:
        dual_result = await execute_dual_mode(query)
        single_result = await execute_single_mode(query)
        
        assert semantic_similarity(
            dual_result.answer, 
            single_result.answer
        ) > 0.9
```

### A/B测试支持

```python
# 支持按比例灰度
if random.random() < SINGLE_LLM_TRAFFIC_PERCENT:
    return await single_mode(query)
else:
    return await dual_mode(query)
```

---

## 执行计划

| 阶段 | 任务 | 工期 | 验收标准 |
|------|------|------|----------|
| 1 | 实现 QueryClassifier | 0.5天 | 单元测试通过，准确率>90% |
| 2 | 实现 UnifiedSynthesizer | 1天 | 单LLM调用成功，输出格式正确 |
| 3 | Graph重构 | 0.5天 | 条件路由正确，fallback工作 |
| 4 | 对比测试 | 1天 | 与双调用模式输出相似度>90% |
| 5 | A/B测试框架 | 0.5天 | 可配置流量比例，指标可观测 |
| 6 | 文档更新 | 0.5天 | 设计文档、API文档更新 |

**总工期: 4天**

---

## 预期收益

| 指标 | 当前 | Phase 2后 | 提升 |
|------|------|-----------|------|
| 平均LLM调用/查询 | 2次 | 1.3次 | -35% |
| 简单查询延迟 | ~3s | ~1.5s | -50% |
| 云端模型成本 | 100% | 65% | -35% |

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 单LLM准确率下降 | 高 | 保持fallback机制，A/B测试验证 |
| 复杂度分类错误 | 中 | 保守策略：不确定时走双调用 |
| 融合Prompt维护难 | 低 | 分离简单/复杂两种prompt模板 |

---

## 下一步

等待确认后，开始实现 Phase 2：
1. 先实现 QueryClassifier（规则+轻量LLM）
2. 再实现 UnifiedSynthesizer（融合Prompt）
3. Graph重构
4. 对比测试验证
