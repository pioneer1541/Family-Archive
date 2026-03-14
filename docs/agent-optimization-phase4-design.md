# Agent V2 Phase 4: 流式输出设计文档

## 目标

实现渐进式响应显示，降低用户感知延迟，提升交互体验。

---

## 现状

当前 Agent 执行是同步阻塞模式：
```
用户Query → [Router LLM] → [检索] → [Synthesizer LLM] → 完整响应
                            ↑
                     用户等待3-5秒才能看到任何内容
```

问题：
- 首token延迟高（3-5秒）
- 用户不知道系统在做什么
- 长答案等待体验差

---

## 方案设计

### 核心思路

1. **节点级流式**：每个节点执行时发送进度事件
2. **Synthesizer流式**：答案生成使用SSE流式传输
3. **前端渐进渲染**：逐步显示内容和思考过程

### 事件流设计

```
START
  ↓
classifier: {complexity: "simple", confidence: 0.95}  ← 即时反馈
  ↓
retrieve: {hit_count: 5, doc_count: 3}              ← 检索进度
  ↓
synthesize: {chunk: "根据文档...", done: false}       ← 流式答案
synthesize: {chunk: "您的护照在...", done: false}
synthesize: {chunk: "", done: true}                  ← 完成
  ↓
END
```

### 技术方案

**后端**：FastAPI SSE (Server-Sent Events)
```python
@router.post("/agent/execute/stream")
async def agent_execute_stream(...) -> StreamingResponse:
    async def event_generator():
        for event in graph.stream_events():
            yield f"data: {json.dumps(event)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )
```

**前端**：EventSource + 渐进渲染
```javascript
const es = new EventSource('/v1/agent/execute/stream');
es.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.chunk) appendChunk(data.chunk);
    if (data.done) es.close();
};
```

---

## 实现计划

| 阶段 | 任务 | 工期 |
|------|------|------|
| 4.1 | Graph 事件流支持 | 0.5天 |
| 4.2 | SSE API 端点 | 0.5天 |
| 4.3 | 前端渐进渲染 | 1天 |
| 4.4 | 集成测试 | 1天 |

**总工期: 3天**

---

## 预期效果

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 首字节时间 | 3-5s | <100ms |
| 用户感知延迟 | 高 | 低 |
| 交互体验 | 等待中... | 实时反馈 |

---

*设计文档 - Phase 4*
