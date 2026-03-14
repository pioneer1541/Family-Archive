# Agent V2 部署指南 - M6全量切换

## 部署前检查清单

- [ ] 所有测试通过 (13 passed)
- [ ] Claude Review无blocking issue
- [ ] 配置默认安全 (AGENT_V2_ENABLED=false)
- [ ] 监控和日志已配置

## 部署步骤

### 1. 预部署验证
```bash
cd /path/to/family-vault/backend
source venv/bin/activate
pytest app/services/agent_v2/tests/ -v
```

### 2. 渐进式灰度

#### 阶段1: 10%流量 (观察1小时)
```bash
export AGENT_V2_ENABLED=true
export AGENT_V2_ROLLOUT_PERCENT=10
export AGENT_V2_METRICS=true
```

观察指标:
- 错误率
- 平均延迟
- 缓存命中率

#### 阶段2: 50%流量 (观察2小时)
```bash
export AGENT_V2_ROLLOUT_PERCENT=50
```

#### 阶段3: 100%流量
```bash
export AGENT_V2_ROLLOUT_PERCENT=100
```

### 3. 回滚方案

如需回滚到V1:
```bash
export AGENT_V2_ENABLED=false
# 或
export AGENT_V2_FORCE=v1
```

无需重启，配置热生效。

## 环境变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| AGENT_V2_ENABLED | false | 是否启用V2 |
| AGENT_V2_ROLLOUT_PERCENT | 0 | 灰度百分比 (0-100) |
| AGENT_V2_FORCE | auto | 强制版本: auto/v1/v2 |
| AGENT_V2_METRICS | true | 是否收集指标 |
| AGENT_V2_DEBUG | false | 是否开启详细日志 |

## 监控指标

查看指标汇总:
```python
from app.services.agent_v2.metrics import get_metrics_summary
print(get_metrics_summary())
```

日志关键词:
- `agent_v2_node_latency` - 节点延迟
- `agent_v2_complete` - 完成统计
- `agent_execute_v2` - V2执行记录
