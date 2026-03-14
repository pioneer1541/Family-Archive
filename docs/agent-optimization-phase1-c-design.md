# Phase 1: LangGraph 重构 - 详细设计文档

## 1. 目标

将现有混合架构（部分LangGraph + 大量手写流程）统一为完整的LangGraph状态机驱动架构，建立可维护、可扩展、可观测的Agent系统基础。

---

## 2. 现状分析

### 2.1 当前架构问题
```
┌─────────────────────────────────────────────────────────────┐
│                     当前混合架构                              │
├─────────────────────────────────────────────────────────────┤
│  agent.py (execute_agent_v2)                                 │
│     │── 手写流程控制                                          │
│     │── 直接调用requests.post (2次LLM调用)                    │
│     │── 状态通过局部变量传递                                   │
│     └── 错误处理/重试逻辑分散                                  │
│                                                              │
│  agent_graph.py (已有但未完全使用)                            │
│     │── LangGraph基础设施已存在                               │
│     └── 仅部分流程使用                                        │
│                                                              │
│  agent_graph_nodes.py (1000+行)                              │
│     └── 节点实现混杂：有LangGraph节点，也有普通函数             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 核心痛点
| 痛点 | 影响 | 具体表现 |
|------|------|---------|
| 状态管理混乱 | 调试困难 | `AgentGraphState` vs 局部变量混用 |
| 流程硬编码 | 扩展性差 | 新增一个处理步骤需改多处 |
| 错误处理分散 | 维护困难 | 每个函数自己try/catch |
| 无法可视化 | 理解困难 | 复杂流程靠脑补 |
| 测试困难 | 质量风险 | 难以单独测试单个节点 |

---

## 3. 目标架构设计

### 3.1 总体架构
```
┌─────────────────────────────────────────────────────────────────┐
│                     LangGraph 状态机架构                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌─────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│   │  START  │───→│  Router  │───→│ Retrieve │───→│ Synthesize│  │
│   └─────────┘    └──────────┘    └──────────┘    └──────────┘  │
│                        │                │              │        │
│                   ┌────┴────┐     ┌────┴────┐    ┌────┴────┐   │
│                   │Chitchat │     │Insufficient│   │  END    │   │
│                   └────┬────┘     └────┬────┘    └─────────┘   │
│                        │               │                        │
│                        └───────→┌─────┴─────┐                   │
│                                 │ Recovery  │                   │
│                                 └───────────┘                   │
│                                                                  │
│   统一状态: AgentGraphState (TypedDict)                          │
│   统一错误处理: 节点级重试 + 全局异常捕获                          │
│   统一观测: 每个节点自动记录耗时和状态                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 模块结构
```
backend/app/services/agent_v2/
├── __init__.py                 # 对外暴露 execute()
├── graph.py                    # LangGraph状态机定义
├── state.py                    # AgentGraphState (迁移现有)
├── nodes/
│   ├── __init__.py
│   ├── router.py              # 意图识别节点
│   ├── retriever.py           # 检索节点
│   ├── synthesizer.py         # 回答生成节点
│   ├── chitchat.py            # 闲聊处理节点
│   ├── recovery.py            # 恢复/重试节点
│   └── utils.py               # 节点通用工具
├── edges/
│   ├── __init__.py
│   ├── conditions.py          # 路由条件函数
│   └── transitions.py         # 边转换逻辑
├── tools/
│   ├── __init__.py
│   ├── search.py              # 搜索工具封装
│   ├── llm.py                 # LLM调用封装
│   └── cache.py               # 缓存工具
└── tests/                     # 独立测试目录
    ├── test_nodes.py
    ├── test_graph.py
    └── test_integration.py
```

---

## 4. 关键技术决策

### 4.1 技术选型
| 组件 | 选择 | 理由 |
|------|------|------|
| LangGraph版本 | 0.2.x | 当前最新稳定版，支持breakpoints和human-in-the-loop |
| 状态管理 | TypedDict | 类型安全，与现有代码兼容 |
| 异步支持 | asyncio | 支持并发节点执行（如并行检索） |
| 持久化 | 可选checkpoint | 支持流程中断恢复（后续扩展） |

### 4.2 节点设计原则
1. **纯函数**: 节点只读state，返回updates，无副作用
2. **单一职责**: 每个节点只做一件事
3. **可观测**: 自动注入日志和metrics
4. **可回退**: 错误时返回fallback结果，不阻断流程

### 4.3 错误处理策略
```python
# 节点级重试
@retry(stop=stop_after_attempt(3), wait=wait_exponential(1, 8))
def router_node(state: AgentGraphState) -> dict:
    try:
        result = call_llm(...)
        return {"route": result}
    except Exception as e:
        logger.error("router_failed", error=str(e))
        return {"route": "fallback", "error": str(e)}

# 全局异常捕获
graph = graph_builder.compile()
graph.with_fallbacks([
    {"condition": lambda e: isinstance(e, LLMError), "node": "recovery_node"}
])
```

---

## 5. 迁移策略

### 5.1 双轨运行方案
为确保平滑迁移，采用"双轨并行 + 灰度切换"策略：

```python
# backend/app/api/routes.py
@router.post("/v1/agent/execute")
def execute_agent(req: AgentExecuteRequest):
    # 通过feature flag控制
    if settings.agent_v2_enabled:
        from app.services.agent_v2 import execute
        return execute(req)
    else:
        from app.services.agent import execute_agent_v2
        return execute_agent_v2(req)
```

### 5.2 分阶段迁移
| 阶段 | 内容 | 工期 | 验证方式 |
|------|------|------|---------|
| 1 | 搭建新架构，实现Router节点 | 3天 | 单元测试通过 |
| 2 | 实现Retrieve + Synthesize节点 | 4天 | 集成测试通过 |
| 3 | 实现Chitchat + Recovery节点 | 3天 | 与旧版输出对比一致 |
| 4 | 双轨并行运行，灰度10%流量 | 3天 | 监控error rate |
| 5 | 全量切换，旧版保留回滚能力 | 2天 | 生产验证1周 |

### 5.3 回滚方案
```python
# 回滚机制
AGENT_V2_ROLLBACK = False  # 紧急情况下置为True

def execute(req):
    if AGENT_V2_ROLLBACK or not settings.agent_v2_enabled:
        return legacy_execute(req)
    return new_execute(req)
```

---

## 6. 核心代码示例

### 6.1 Graph定义
```python
# backend/app/services/agent_v2/graph.py
from langgraph.graph import StateGraph, START, END
from app.services.agent_v2.state import AgentGraphState
from app.services.agent_v2.nodes import router, retriever, synthesizer, chitchat, recovery
from app.services.agent_v2.edges import should_chitchat, should_recover

builder = StateGraph(AgentGraphState)

# 节点
builder.add_node("router", router.node)
builder.add_node("chitchat", chitchat.node)
builder.add_node("retrieve", retriever.node)
builder.add_node("synthesize", synthesizer.node)
builder.add_node("recovery", recovery.node)

# 边
builder.add_edge(START, "router")
builder.add_conditional_edges("router", should_chitchat, {
    True: "chitchat",
    False: "retrieve"
})
builder.add_conditional_edges("retrieve", should_recover, {
    True: "recovery",
    False: "synthesize"
})
builder.add_edge("chitchat", END)
builder.add_edge("synthesize", END)
builder.add_edge("recovery", "retrieve")  # 重试

graph = builder.compile()
```

### 6.2 节点实现示例
```python
# backend/app/services/agent_v2/nodes/router.py
from typing import Any
from app.services.agent_v2.state import AgentGraphState
from app.services.agent_v2.tools.llm import call_router_llm
import structlog

logger = structlog.get_logger()

async def router_node(state: AgentGraphState) -> dict[str, Any]:
    """
    意图识别节点
    输入: state["req"] (用户查询)
    输出: {route: str, route_reason: str, confidence: float}
    """
    query = state["req"]["query"]
    
    # 缓存检查
    cache_key = f"router:{hash(query)}"
    if cached := await cache.get(cache_key):
        logger.info("router_cache_hit", query=query)
        return {"router": cached}
    
    try:
        result = await call_router_llm(query)
        
        # 写入缓存
        await cache.set(cache_key, result, ttl=300)
        
        logger.info("router_success", 
                   query=query, 
                   route=result["route"],
                   confidence=result["confidence"])
        
        return {"router": result}
        
    except Exception as e:
        logger.error("router_failed", query=query, error=str(e))
        # 返回fallback，不阻断流程
        return {
            "router": {
                "route": "lookup",
                "route_reason": "llm_error_fallback",
                "confidence": 0.5
            }
        }
```

### 6.3 执行入口
```python
# backend/app/services/agent_v2/__init__.py
from typing import Any
import time
from app.services.agent_v2.graph import graph
from app.services.agent_v2.state import AgentGraphState
from app.schemas import AgentExecuteRequest, AgentExecuteResponse

async def execute(req: AgentExecuteRequest) -> AgentExecuteResponse:
    """新架构执行入口"""
    
    # 初始化状态
    initial_state: AgentGraphState = {
        "req": req.model_dump(),
        "trace_id": f"agt-{uuid.uuid4().hex[:12]}",
        "timing": {"start_ms": int(time.time() * 1000)}
    }
    
    # 执行图
    result = await graph.ainvoke(initial_state)
    
    # 构造响应
    return AgentExecuteResponse(
        card=result["final_card_payload"],
        planner=result["router"],
        executor_stats=result["executor_stats_payload"],
        trace_id=result["trace_id"]
    )
```

---

## 7. 风险清单与应对

| 风险 | 等级 | 影响 | 应对措施 |
|------|------|------|---------|
| 新架构bug导致服务不可用 | 高 | 生产事故 | 双轨运行，保留回滚能力，灰度发布 |
| LangGraph性能不如预期 | 中 | 延迟增加 | 基准测试对比，不达标则优化或回滚 |
| 状态迁移数据丢失 | 中 | 用户体验差 | 增加状态验证和fallback逻辑 |
| 开发周期超预期 | 低 | 延期 | 分阶段交付，每阶段可独立上线 |
| 与现有缓存/日志系统冲突 | 低 | 观测困难 | 提前测试集成点 |

---

## 8. 测试策略

### 8.1 测试金字塔
```
        /\
       /  \
      / E2E \     # 完整流程测试（10%）
     /--------\
    / Integration\ # 节点间集成测试（20%）
   /--------------\
  /   Unit Tests   \ # 单节点测试（70%）
 /------------------\
```

### 8.2 关键测试用例
| 类型 | 测试点 | 验证目标 |
|------|--------|---------|
| 单元 | Router节点 | 输入→输出正确，错误处理正常 |
| 单元 | 各边条件 | 状态→路由判断正确 |
| 集成 | Router→Retrieve→Synthesize | 完整链路通顺 |
| E2E | 与旧版输出对比 | 1000条查询结果一致性>95% |
| 性能 | 延迟对比 | P95延迟不劣于旧版 |

### 8.3 对比测试脚本
```python
# 新旧版输出对比
async def compare_implementations():
    test_queries = load_test_queries()  # 1000条真实查询
    
    for query in test_queries:
        old_result = await legacy_execute(query)
        new_result = await new_execute(query)
        
        assert semantic_similarity(old_result, new_result) > 0.95
```

---

## 9. 工期与里程碑

| 里程碑 | 内容 | 工期 | 交付物 |
|--------|------|------|--------|
| M1 | 架构搭建 + Router节点 | 3天 | 可运行的Router，单测通过 |
| M2 | Retrieve + Synthesize节点 | 4天 | 完整链路跑通 |
| M3 | Chitchat + Recovery + 工具 | 3天 | 所有节点实现完成 |
| M4 | 集成测试 + 对比测试 | 3天 | 测试报告，一致性>95% |
| M5 | 灰度发布 + 监控 | 3天 | 生产环境10%流量 |
| M6 | 全量切换 | 2天 | 完全替换，旧版代码保留 |

**总工期**: 2-3周（含buffer）

---

## 10. 已确认决策

| # | 问题 | 决策 | 备注 |
|---|------|------|------|
| 1 | 旧版代码保留 | **删除**（不长期共存） | 通过git回滚 |
| 2 | 灰度节奏 | **直接100%** | 激进切换 |
| 3 | 异步并发节点 | **Phase 1不做** | Phase 2按需 |
| 4 | Checkpoint | **SQLite实现** | M5灰度启用 |
| 5 | Dify关系 | **完全不考虑** | 独立项目 |

---

## 11. 风险确认

**高风险决策组合**：
- 旧代码删除 + 直接100% = 无热切换能力，故障时需git回滚
- 是否接受？已接受

---

## 12. 下一步

立即开始M1开发：
1. 创建feature分支 `feature/agent-v2-langgraph`
2. 搭建基础架构
3. 实现Router节点
4. 每日站会对齐

**当前状态**: ✅ 设计完成，决策已确认，准备开发
