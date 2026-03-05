# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- Gmail 密钥管理
  - GmailCredentials 数据模型
  - Gmail OAuth 凭证加密存储
  - API 端点：GET/POST/PUT/DELETE /api/v1/gmail/credentials
  - 凭证显示脱敏处理

- 多用户系统
  - User 模型和数据库迁移
  - 用户注册、登录、登出 API（/api/v1/auth/*）
  - 密码修改、账户删除（软删除）
  - JWT 包含 user_id 和 role
  - 角色区分（admin/user）
  - 保留旧 admin 迁移兼容性

- LLM 多提供商支持
  - 支持 Ollama、OpenAI、Kimi、GLM、Custom 五种提供商
  - API Key 加密存储 (Fernet 对称加密)
  - LLM Router 支持本地/云端回退机制
  - 数据库迁移脚本 (llm_providers 表)
  - Settings UI 添加提供商管理界面

- 服务管理
  - /api/v1/restart API 端点 (重启 Worker 应用配置变更)
  - 设置变更检测 (PATCH /settings 返回 restart_required)
  - Worker 容器内 Docker socket 通信支持

- Bug 修复
  - JWT 环境变量配置一致性 (统一使用 FAMILY_VAULT_JWT_SECRET)
  - 前端构建目录路径 (修正 .next-runtime 目录)
  - Fernet 加密密钥处理 (移除多余的 base64 decode)
  - Settings 保存响应状态检查 (正确处理 4xx/5xx 错误)

- Docker 化支持
  - `install.sh` 一键安装脚本
  - `update.sh` 更新脚本 (含自动备份和回滚)
  - `backup.sh` 备份脚本 (支持完整/快速/仅数据三种模式)
  - `docker-compose.full.yml` 完整版 (含 Nginx + Ollama)
  - 生产级 Dockerfile (非 root 用户运行)

- 安全增强
  - Docker 镜像使用非 root 用户
  - 备份文件权限限制 (chmod 600)
  - JWT_SECRET 通过环境变量注入
  - Nginx 反向代理配置

- 文档
  - README.md 重构 (双语、清晰分类)
  - docs/QUICKSTART.md 详细安装指南

### Changed

- docker-compose.yml 移除硬编码密钥
- nginx.conf 修复变量缺失问题
- 服务健康检查优化

---

## [1.0.0] - 2025-02-XX

### Added

- 初始版本
- 文档管理核心功能
  - PDF/DOCX/XLSX/TXT 文档解析
  - OCR 扫描件识别
  - 向量检索 + 全文搜索
- AI 功能
  - Ollama 本地推理
  - 文档摘要
  - 智能问答
  - 自动分类
- 用户界面
  - 双语支持 (中文/英文)
  - 密码保护
  - Settings UI
- 集成
  - NAS 目录同步
  - Gmail 附件导入

---

## 版本命名规范

- **主版本号 (Major)**: 不兼容的 API 变更
- **次版本号 (Minor)**: 向后兼容的功能新增
- **修订号 (Patch)**: 向后兼容的问题修复

示例：
- `1.0.0` → `1.0.1`: Bug 修复
- `1.0.0` → `1.1.0`: 新功能
- `1.0.0` → `2.0.0`: 重大变更

---

## 发布检查清单

- [ ] 更新 CHANGELOG.md
- [ ] 更新 package.json / pyproject.toml 版本号
- [ ] 运行测试套件
- [ ] 构建并测试 Docker 镜像
- [ ] 创建 Git tag
- [ ] 推送到 GitHub
- [ ] 构建 Docker Hub 镜像
- [ ] 发布 GitHub Release
