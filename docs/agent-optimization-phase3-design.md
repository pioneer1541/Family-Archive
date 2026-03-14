# Agent V2 Phase 3: P1 快速优化设计文档

## 目标

在 Phase 2 单LLM模式基础上，进一步优化性能和成本：
- **Router 结果缓存**: 减少 30-50% Router 调用
- **Chitchat 短路优化**: 问候类查询 0 LLM 调用
- **模型分级**: Router 用轻量模型，成本降低 60%+

---

## 优化项 1: Router 结果缓存

### 问题
相同查询在短时间内重复调用 Router LLM，造成浪费。

### 方案
基于 query hash 缓存 Router 决策结果，TTL 5-10 分钟。

```python
# 缓存键: hash(query + ui_lang + doc_scope)
cache_key = hashlib.md5(f"{query}:{ui_lang}:{doc_scope}".encode()).hexdigest()

# 缓存值
{
    "route": "lookup",
    "confidence": 0.9,
    "rewritten_query": "...",
    "route_reason": "llm",
    "cached_at": 1234567890
}
```

### 实现
- **存储**: SQLite (已在 Phase 1 中使用)
- **TTL**: 5 分钟（可配置）
- **命中率**: 预期 30-50%

---

## 优化项 2: Chitchat 短路优化

### 现状
Chitchat 在 Router 节点中检测，仍需一次 LLM 调用。

### 优化
在 QueryClassifier 阶段前置 Chitchat 检测：

```python
def _is_chitchat_quick(query: str) -> bool:
    """快速检测问候语，零成本"""
    q = query.lower().strip()
    if len(q) <= 10:
        return any(pattern in q for pattern in _CHITCHAT_PATTERNS)
    return False
```

### 流程变更
```
START → QueryClassifier
           ↓
    ┌──────┴──────┐
  chitchat      other
    ↓              ↓
直接响应    classifier → unified/router
```

---

## 优化项 3: 模型分级

### 问题
Router 和 Synthesizer 使用同等级模型，Router 不需要强模型。

### 方案
| 用途 | 当前模型 | 优化后 | 成本对比 |
|------|---------|--------|---------|
| Router | `glm-4-plus` | `glm-4-flash` | 降低 80% |
| Synthesizer | `glm-4-plus` | `glm-4-plus` | 保持不变 |

### 配置
```python
# config.py
ROUTER_MODEL: str = "glm-4-flash"  # 轻量模型
SYNTHESIZER_MODEL: str = "glm-4-plus"  # 强模型
```

---

## 执行计划

| 阶段 | 任务 | 工期 | 验收标准 |
|------|------|------|----------|
| 3.1 | Router 缓存实现 | 0.5天 | 缓存命中率>30%，单元测试通过 |
| 3.2 | Chitchat 前置优化 | 0.5天 | 问候类查询0 LLM调用 |
| 3.3 | 模型分级配置 | 0.5天 | Router使用轻量模型 |
| 3.4 | 集成测试 | 0.5天 | 整体性能提升验证 |

**总工期: 2天**

---

## 预期收益

| 指标 | Phase 2 | Phase 3 | 累计提升 |
|------|---------|---------|---------|
| 平均LLM调用/查询 | 1.3次 | 1.0次 | -50% |
| 简单查询成本 | 65% | 35% | -65% |
| 响应延迟 | ~1.5s | ~1.2s | -60% |

---

*设计文档 - Phase 3*
