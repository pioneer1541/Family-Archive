# Docker Hub 发布说明

## 镜像标签规范

```
pioneer1541/family-vault:latest          # 最新稳定版
pioneer1541/family-vault:1.0.0           # 具体版本
pioneer1541/family-vault:1.0             # 主版本.次版本
pioneer1541/family-vault:1.0.0-alpine    # Alpine 变体 (可选)
```

---

## Docker Hub 描述 (Description)

### Short Description (100 字符内)

Family Vault - 私有家庭文档智能管理助手，本地 AI，数据不离家

### Full Description

---

# Family Vault

> 私有家庭文档智能管理助手 - 本地 AI，数据不离家

## 🚀 快速开始

```bash
# 克隆项目
git clone https://github.com/pioneer1541/Family-Archive.git
cd Family-Archive

# 一键安装
./install.sh
```

## 📦 镜像包含

此镜像包含 Family Vault 后端服务：

- FastAPI 应用
- Celery Worker
- Qdrant 向量数据库客户端
- OCR 支持 (Tesseract)

## 🔧 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `FAMILY_VAULT_JWT_SECRET` | JWT 密钥 (必须) | - |
| `FAMILY_VAULT_DATABASE_URL` | 数据库路径 | `sqlite:///./data/family_vault.db` |
| `FAMILY_VAULT_REDIS_URL` | Redis 地址 | `redis://redis:6379/0` |
| `FAMILY_VAULT_QDRANT_URL` | Qdrant 地址 | `http://qdrant:6333` |
| `FAMILY_VAULT_OLLAMA_BASE_URL` | Ollama 地址 | `http://host.docker.internal:11434` |

## 📖 完整文档

- [GitHub](https://github.com/pioneer1541/Family-Archive)
- [快速入门](https://github.com/pioneer1541/Family-Archive/blob/main/docs/QUICKSTART.md)

## 📝 许可证

MIT License

---

## Dockerfile 标签建议

```dockerfile
# 在 Dockerfile 中添加
LABEL org.opencontainers.image.title="Family Vault"
LABEL org.opencontainers.image.description="Private family document management assistant with local AI"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.authors="pioneer1541"
LABEL org.opencontainers.image.url="https://github.com/pioneer1541/Family-Archive"
LABEL org.opencontainers.image.source="https://github.com/pioneer1541/Family-Archive"
LABEL org.opencontainers.image.licenses="MIT"
```

---

## 发布命令

```bash
# 构建镜像
docker build -t pioneer1541/family-vault:1.0.0 -t pioneer1541/family-vault:latest ./backend

# 推送到 Docker Hub
docker push pioneer1541/family-vault:1.0.0
docker push pioneer1541/family-vault:latest

# 前端镜像
docker build -t pioneer1541/family-vault-frontend:1.0.0 -t pioneer1541/family-vault-frontend:latest ./frontend
docker push pioneer1541/family-vault-frontend:1.0.0
docker push pioneer1541/family-vault-frontend:latest
```
