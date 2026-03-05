# GitHub Release Notes 模板

---

## Release v1.0.0

### 🎉 首次发布

Family Vault 是一个私有的家庭文档智能管理助手，支持 PDF、DOCX、扫描件、照片等文档的智能索引和问答。所有 AI 推理本地运行，数据不离家。

### ✨ 主要功能

- 📄 **文档解析** - PDF、DOCX、XLSX、TXT、图片；扫描件 OCR
- 🤖 **本地 AI** - 文档摘要、分类、问答 (Ollama)
- 🔍 **智能搜索** - 向量检索 + 全文搜索
- 📁 **NAS 同步** - 自动扫描挂载目录
- 📧 **Gmail 集成** - 自动导入邮件附件
- 🌐 **双语界面** - 简体中文 + English
- 🔒 **密码保护** - bcrypt 加密存储

### 🚀 快速开始

```bash
# 克隆项目
git clone https://github.com/pioneer1541/Family-Archive.git
cd Family-Archive

# 一键安装
./install.sh

# 访问
open http://localhost:18181
```

### 📦 系统要求

| 项目 | 最低 | 推荐 |
|------|------|------|
| CPU | 2 核 | 4 核+ |
| 内存 | 4 GB | 8 GB+ |
| 磁盘 | 10 GB | 50 GB+ |

### 📝 更新日志

详见 [CHANGELOG.md](CHANGELOG.md)

### 📖 文档

- [快速入门指南](docs/QUICKSTART.md)
- [环境变量配置](docs/ENVIRONMENT.md)
- [API 文档](docs/API.md)

### 🐛 已知问题

- [ ] 大文件上传可能需要较长时间
- [ ] 某些 PDF 格式 OCR 效果不佳

### 🙏 致谢

感谢以下开源项目：
- [FastAPI](https://fastapi.tiangolo.com/)
- [Ollama](https://ollama.com)
- [Qdrant](https://qdrant.tech)
- [Streamlit](https://streamlit.io) / [Next.js](https://nextjs.org)

---

## 发布检查清单

发布新版本时，更新以下内容：

### 代码准备

- [ ] 更新版本号
  - [ ] `backend/app/__init__.py`
  - [ ] `frontend/package.json`
  - [ ] `pyproject.toml`

- [ ] 更新文档
  - [ ] `CHANGELOG.md`
  - [ ] `README.md` (如有必要)

- [ ] 测试
  - [ ] 单元测试通过
  - [ ] Docker 构建成功
  - [ ] 功能测试通过

### Git 操作

```bash
# 创建 tag
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

### GitHub Release

1. 进入 Releases → Draft a new release
2. 选择 tag
3. 填写 Release title: `v1.0.0`
4. 粘贴 Release Notes
5. 上传附件 (如有)
6. Publish release

### Docker Hub

```bash
# 构建并推送
docker build -t pioneer1541/family-vault:1.0.0 ./backend
docker push pioneer1541/family-vault:1.0.0

# 更新 latest
docker tag pioneer1541/family-vault:1.0.0 pioneer1541/family-vault:latest
docker push pioneer1541/family-vault:latest
```

### 公告

- [ ] GitHub Discussions
- [ ] Discord 社区
- [ ] Twitter/X (如有)

---

## 版本号说明

遵循 [Semantic Versioning](https://semver.org/):

- **MAJOR**: 不兼容的 API 变更
- **MINOR**: 向后兼容的功能新增
- **PATCH**: 向后兼容的问题修复
