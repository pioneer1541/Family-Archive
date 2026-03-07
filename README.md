# Family Vault

> 家庭文档智能管理助手 - 私有部署，数据不离家

Family Vault 是一个自托管的家庭文档管理系统，支持 PDF、DOCX、扫描件、照片等文档的智能索引和问答。所有 AI 推理通过 [Ollama](https://ollama.com) 本地运行，无需云端 API。

---

## ✨ 功能特性

- **🌐 双语界面** — 简体中文 + English 切换
- **📄 文档解析** — PDF、DOCX、XLSX、TXT、图片；扫描件 OCR 识别
- **🤖 多 LLM 支持** — Ollama 本地推理 / OpenAI / Kimi / GLM / 自定义 API
- **🔍 智能搜索** — Qdrant 向量检索 + SQLite 全文搜索
- **📊 Agent 问答** — 自然语言提问，引用原文回答
- **🔄 后台处理** — Celery 异步任务，大文件分页处理
- **📁 NAS 同步** — 自动扫描挂载目录
- **📧 Gmail 集成** — 自动导入邮件附件
- **⚙️ 可视化配置** — Settings UI 无需编辑配置文件
- **🔐 多用户系统** — 用户注册、登录、角色管理，支持多账户

---

## 🤖 LLM 提供商支持

Family Vault 支持多种 LLM 提供商，可在设置界面中切换：

| 提供商 | 说明 | 配置项 |
|--------|------|--------|
| Ollama | 本地推理 (默认) | 基础 URL + 模型名 |
| OpenAI | GPT 系列 | API Key + 模型 |
| Kimi | 月之暗面 | API Key + 模型 |
| GLM | 智谱清言 | API Key + 模型 |
| Custom | 自定义 OpenAI 兼容 API | URL + Key + 模型 |

**配置方式：**
1. 访问 Settings → LLM Providers
2. 添加提供商并输入 API Key (加密存储)
3. 在模型选择中切换提供商

**回退机制：**
- 可配置本地 Ollama 作为云端 API 的回退
- 当云端 API 不可用时自动切换到本地模型

---


## 🏗️ 后端架构

Family Vault 后端采用模块化架构，核心 Agent 服务由以下模块组成：

### Agent 模块结构

```
backend/app/services/
├── agent.py (1289 行) — 主入口，协调各模块
├── agent_constants.py — 常量和 QueryFacet 定义
├── query_policy.py — 查询理解和 facet 检测
├── evidence.py — 证据评估和覆盖率计算
├── detail_extract.py — 细节字段提取
├── docs.py — 文档辅助函数
├── bills.py — Bill 相关工具函数
├── queue_reprocess.py — 队列和重处理逻辑
├── agent_utils.py — JSON/Scope 工具函数
├── agent_actions.py — Action 构建器
├── agent_router_adapter.py — Router 和 Chitchat 适配器
├── agent_bundle_search.py — 搜索 Bundle 构建
├── planner.py — LLM Planner 路由决策
├── search.py — 向量 + 全文混合搜索
├── qdrant.py — Qdrant 向量数据库操作
├── ingestion.py — 文档摄取和分块
├── llm_router.py — 多 LLM 提供商路由
└── ... — 其他服务模块
```

### 核心流程

```
用户查询 → Planner (LLM) → Router → Bundle Builder → Search → Evidence → Synthesis
```

1. **Planner** — LLM 分析用户意图，决定路由策略
2. **Router** — 根据 Planner 决策选择 Bundle 类型
3. **Bundle Builder** — 构建上下文（搜索/账单/详情等）
4. **Search** — Qdrant 向量搜索 + SQLite 全文搜索
5. **Evidence** — 评估答案覆盖率和可信度
6. **Synthesis** — LLM 生成最终答案

### 模块职责

| 模块 | 职责 |
|------|------|
| `agent.py` | 协调各模块，执行查询流程 |
| `query_policy.py` | 查询理解、facet 检测、上下文策略 |
| `evidence.py` | 证据评估、覆盖率计算、可答性推断 |
| `detail_extract.py` | 细节字段提取、JSON 序列化 |
| `docs.py` | 文档去重、相关文档构建 |
| `bills.py` | Bill 相关日期/金额格式化 |
| `agent_bundle_search.py` | 搜索 Bundle 构建（核心检索逻辑） |
| `planner.py` | LLM 路由决策 |
| `search.py` | 混合搜索（向量 + 全文） |

---

## 📋 系统要求

| 项目 | 最低要求 | 推荐配置 |
|------|----------|----------|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 10 GB | 50 GB+ (含文档存储) |
| Docker | 20.10+ | 最新版 |
| Docker Compose | v2.0+ | 最新版 |
| Ollama | 运行中 | 运行中 |

**Ollama 模型要求：**

```bash
# 推荐 (8GB+ RAM)
ollama pull qwen3:1.7b              # Planner
ollama pull qwen3:4b-instruct       # Synthesis
ollama pull qwen3-embedding:0.6b    # Embeddings
ollama pull lfm2                    # Summarisation

# 最小 (4GB RAM)
ollama pull qwen3:1.7b
ollama pull qwen3-embedding:0.6b
```

---

## 🚀 快速开始

### 方式一：一键安装 (推荐)

```bash
# 1. 克隆项目
git clone https://github.com/pioneer1541/Family-Archive.git
cd Family-Archive

# 2. 运行安装脚本
./install.sh
```

安装脚本会自动：
- ✅ 检查 Docker 环境
- ✅ 创建必要目录
- ✅ 生成 JWT 密钥
- ✅ 构建并启动服务

### 方式二：手动安装

```bash
# 1. 克隆项目
git clone https://github.com/pioneer1541/Family-Archive.git
cd Family-Archive

# 2. 创建配置文件
cp .env.example .env
sed -i "s|<replace-with-your-secret>|$(openssl rand -hex 32)|" .env

# 3. 构建并启动
docker compose up -d
```

### 访问应用

- **前端界面**: http://localhost:18181
- **API 文档**: http://localhost:18180/docs
- **Redis**: localhost:16379
- **Qdrant**: http://localhost:16333

首次访问会提示设置管理员密码。

---

## ⚙️ 配置说明

### 环境变量

主要配置项 (在 `.env` 文件中设置)：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FAMILY_VAULT_JWT_SECRET` | JWT 密钥 (必须修改) | - |
| `FAMILY_VAULT_DATABASE_URL` | 数据库路径 | SQLite |
| `FAMILY_VAULT_OLLAMA_BASE_URL` | Ollama 服务地址 | http://host.docker.internal:11434 |
| `FAMILY_VAULT_NAS_DEFAULT_SOURCE_DIR` | NAS 源目录 | - |

### NAS 挂载

编辑 `docker-compose.yml`，添加挂载：

```yaml
services:
  fkv-api:
    volumes:
      - /mnt/nas:/mnt/nas:ro  # 只读挂载
  fkv-worker:
    volumes:
      - /mnt/nas:/mnt/nas:ro
```

然后在 Settings → Storage & Scan 中设置源目录路径。

### HTTPS 配置

使用 `docker-compose.full.yml` 启用 Nginx 反向代理：

```bash
# 放置 SSL 证书
mkdir -p nginx/ssl
cp your-cert.pem nginx/ssl/cert.pem
cp your-key.pem nginx/ssl/key.pem

# 启动完整版 (含 Nginx)
docker compose --profile full up -d
```

---

## 📖 常用命令

```bash
# 查看日志
docker compose logs -f

# 查看特定服务日志
docker compose logs -f fkv-api

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 更新服务
./update.sh

# 备份数据
./backup.sh              # 完整备份
./backup.sh --quick      # 快速备份 (仅数据库+配置)
./backup.sh --keep 20    # 保留最近 20 个备份
```

---

## ❓ 常见问题

### Q: 首次启动很慢？

A: 首次启动需要下载基础镜像和构建项目，通常需要 5-10 分钟。后续启动会快很多。

### Q: Ollama 连接失败？

A: 确保 Ollama 正在运行，并且 `.env` 中的 `FAMILY_VAULT_OLLAMA_BASE_URL` 正确：
- macOS/Windows: `http://host.docker.internal:11434`
- Linux: 需要配置 `extra_hosts`

### Q: 上传文档后没有反应？

A: 检查 Worker 服务状态：
```bash
docker compose logs fkv-worker
```
确保模型已下载并且 Ollama 正常运行。

### Q: 如何更换端口？

A: 修改 `docker-compose.yml` 中的 `ports` 配置：
```yaml
ports:
  - "你的端口:18080"  # API
  - "你的端口:18081"  # 前端
```

### Q: 数据存储在哪里？

A: 所有数据存储在 `./data` 目录：
- `data/family_vault.db` - SQLite 数据库
- `data/qdrant/` - 向量索引
- `data/mail_attachments/` - 邮件附件

---

## 🔄 更新与备份

### 更新

```bash
./update.sh
```

更新脚本会：
- 自动备份数据库
- 拉取最新代码 (如果是 git 仓库)
- 重新构建镜像
- 重启服务
- 失败自动回滚

### 备份

```bash
# 完整备份
./backup.sh

# 快速备份 (仅数据库和配置)
./backup.sh --quick

# 恢复
tar -xzf family_vault_backup_YYYYMMDD_HHMMSS.tar.gz
cp family_vault_backup_*/family_vault.db data/
cp family_vault_backup_*/.env .
```

---

## 📚 更多文档

- [快速入门指南](docs/QUICKSTART.md) - 详细安装步骤

---

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE)

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

详见 [CONTRIBUTING.md](CONTRIBUTING.md)
