# Family Vault 快速入门指南

本指南帮助你从零开始部署 Family Vault。

---

## 目录

1. [系统准备](#1-系统准备)
2. [安装 Docker](#2-安装-docker)
3. [安装 Ollama](#3-安装-ollama)
4. [下载模型](#4-下载模型)
5. [部署 Family Vault](#5-部署-family-vault)
6. [配置](#6-配置)
7. [添加文档](#7-添加文档)
8. [常见问题](#8-常见问题)

---

## 1. 系统准备

### 硬件要求

| 配置 | CPU | 内存 | 磁盘 |
|------|-----|------|------|
| 最低 | 2 核 | 4 GB | 10 GB |
| 推荐 | 4 核+ | 8 GB+ | 50 GB+ |

### 操作系统支持

- macOS 12+ (Monterey)
- Windows 10/11 + WSL2
- Ubuntu 20.04+ / Debian 11+
- 其他 Linux 发行版 (需支持 Docker)

---

## 2. 安装 Docker

### macOS

1. 下载 [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop)
2. 安装并启动
3. 验证安装：

```bash
docker --version
docker compose version
```

### Windows

1. 启用 WSL2：
   ```powershell
   wsl --install
   ```
2. 下载 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop)
3. 安装时选择 WSL2 后端
4. 验证安装：
   ```bash
   docker --version
   docker compose version
   ```

### Linux (Ubuntu/Debian)

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | sh

# 添加当前用户到 docker 组
sudo usermod -aG docker $USER

# 重新登录后验证
docker --version
docker compose version
```

---

## 3. 安装 Ollama

### macOS

```bash
# 下载安装
curl -fsSL https://ollama.com/install.sh | sh

# 或使用 Homebrew
brew install ollama
```

### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Windows

下载 [Ollama for Windows](https://ollama.com/download/windows)

### 启动 Ollama

```bash
# 启动服务
ollama serve

# 验证 (新终端)
ollama list
```

---

## 4. 下载模型

### 推荐配置 (8GB+ RAM)

```bash
ollama pull qwen3:1.7b              # Planner - 路由和轻量问答
ollama pull qwen3:4b-instruct       # Synthesis - 问答合成
ollama pull qwen3-embedding:0.6b    # Embeddings - 向量嵌入
ollama pull lfm2                    # Summarisation - 文档摘要
```

### 最小配置 (4GB RAM)

```bash
ollama pull qwen3:1.7b
ollama pull qwen3-embedding:0.6b
```

### 验证模型

```bash
ollama list
# 应该显示已下载的模型
```

---

## 5. 部署 Family Vault

### 方式一：一键安装 (推荐)

```bash
# 克隆项目
git clone https://github.com/pioneer1541/Family-Archive.git
cd Family-Archive

# 运行安装脚本
./install.sh
```

安装过程会提示：
- 是否重新生成配置 (如果 .env 已存在)
- NAS 目录路径 (可选)

### 方式二：手动安装

```bash
# 1. 克隆项目
git clone https://github.com/pioneer1541/Family-Archive.git
cd Family-Archive

# 2. 创建配置文件
cp .env.example .env

# 3. 生成 JWT 密钥
# macOS/Linux
sed -i "s|<replace-with-your-secret>|$(openssl rand -hex 32)|" .env

# 或手动编辑 .env，设置 JWT_SECRET

# 4. 构建镜像
docker compose build

# 5. 启动服务
docker compose up -d

# 6. 查看日志
docker compose logs -f
```

### 验证部署

```bash
# 检查服务状态
docker compose ps

# 应该看到 fkv-api, fkv-frontend, redis, qdrant 都在运行
```

---

## 6. 配置

### 首次访问

打开浏览器访问 **http://localhost:18181**

1. 设置管理员密码
2. (可选) 配置 Ollama URL
3. 开始使用

### Settings 页面配置

| 设置项 | 说明 |
|--------|------|
| Storage & Scan | NAS 源目录、自动扫描间隔 |
| Models | AI 模型选择 |
| Connectivity | Ollama 连接测试 |

### NAS 挂载配置

1. 编辑 `docker-compose.yml`：

```yaml
services:
  fkv-api:
    volumes:
      - /你的/NAS/路径:/mnt/nas:ro
  fkv-worker:
    volumes:
      - /你的/NAS/路径:/mnt/nas:ro
```

2. 重启服务：
```bash
docker compose down
docker compose up -d
```

3. 在 Settings → Storage & Scan 设置源目录为 `/mnt/nas`

### 环境变量配置

编辑 `.env` 文件：

```bash
# AI 模型配置
FAMILY_VAULT_OLLAMA_BASE_URL=http://host.docker.internal:11434
FAMILY_VAULT_SUMMARY_MODEL=lfm2:latest
FAMILY_VAULT_CATEGORY_MODEL=qwen3:4b-instruct

# NAS 配置
FAMILY_VAULT_NAS_DEFAULT_SOURCE_DIR=/mnt/nas
FAMILY_VAULT_NAS_AUTO_SCAN_ENABLED=1
FAMILY_VAULT_NAS_SCAN_INTERVAL_SEC=900

# 邮件配置 (可选)
FAMILY_VAULT_MAIL_POLL_ENABLED=1
```

修改后重启：
```bash
docker compose restart
```

---

## 7. 添加文档

### 方式一：拖放上传

1. 进入 Documents 页面
2. 拖放文件到上传区域
3. 等待处理完成

### 方式二：NAS 同步

1. 配置 NAS 挂载 (见上文)
2. 在 Settings → Storage & Scan 设置源目录
3. 启用自动扫描
4. 文档会自动导入

### 方式三：Gmail 附件

1. 准备 Gmail API 凭证
2. 放置到 `secrets/gmail/` 目录
3. 在 `.env` 启用邮件轮询

---

## 8. 常见问题

### Ollama 连接失败

**症状**: 上传文档后无响应，日志显示 Ollama 连接错误

**解决方案**:

1. 确认 Ollama 正在运行：
   ```bash
   curl http://localhost:11434/api/tags
   ```

2. 检查 Docker 配置：
   - macOS/Windows: 确保 `host.docker.internal` 可用
   - Linux: 添加 `extra_hosts` 到 docker-compose.yml：
     ```yaml
     extra_hosts:
       - "host.docker.internal:host-gateway"
     ```

### 文档处理失败

**症状**: 文档上传后显示错误

**解决方案**:

1. 检查 Worker 日志：
   ```bash
   docker compose logs fkv-worker
   ```

2. 常见原因：
   - 模型未下载：运行 `ollama pull <model>`
   - 内存不足：检查系统内存
   - 文件格式不支持：检查文件扩展名

### Qdrant 错误

**症状**: 向量索引失败

**解决方案**:

```bash
# 重启 Qdrant
docker compose restart qdrant

# 检查 Qdrant 健康状态
curl http://localhost:16333/healthz
```

### 密码忘记

**解决方案**:

```bash
# 重置密码
docker compose exec fkv-api python -c "
from app.core.security import get_password_hash
print(get_password_hash('新密码'))
"
# 然后更新数据库中的密码
```

---

## 下一步

- 🔄 [更新与备份](../README.md#更新与备份)

---

## 获取帮助

- [GitHub Issues](https://github.com/pioneer1541/Family-Archive/issues)
- [Discord 社区](https://discord.gg/clawd)
