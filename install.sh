#!/bin/bash
set -e

# ============================================
# Family Vault 一键安装脚本
# ============================================

# 颜色定义
RED='\e[0;31m'
GREEN='\e[0;32m'
YELLOW='\e[1;33m'
BLUE='\e[0;34m'
NC='\e[0m'

# 日志函数
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 错误处理
trap 'log_error "安装失败，请检查错误信息"; exit 1' ERR

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "============================================"
echo "   Family Vault 安装向导"
echo "============================================"
echo ""

# 1. 检查依赖
log_info "检查系统依赖..."

if ! command -v docker &> /dev/null; then
    log_error "Docker 未安装，请先安装 Docker"
    log_info "安装指南: https://docs.docker.com/get-docker/"
    exit 1
fi

if ! docker compose version &> /dev/null && ! command -v docker-compose &> /dev/null; then
    log_error "Docker Compose 未安装，请先安装 Docker Compose"
    log_info "安装指南: https://docs.docker.com/compose/install/"
    exit 1
fi

log_success "Docker 和 Docker Compose 已安装"

# 2. 检查 Docker 服务
log_info "检查 Docker 服务状态..."
if ! docker info &> /dev/null; then
    log_error "Docker 服务未运行，请启动 Docker"
    exit 1
fi
log_success "Docker 服务正常"

# 3. 创建必要目录
log_info "创建必要目录..."
mkdir -p data
mkdir -p data/qdrant
mkdir -p data/mail_attachments
mkdir -p data/email_attachments
mkdir -p models
mkdir -p secrets/gmail
mkdir -p nginx/ssl
log_success "目录创建完成"

# 4. 配置环境变量
GENERATE_ENV=false
if [ -f ".env" ]; then
    log_warn ".env 文件已存在"
    read -p "是否重新生成配置? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cp .env ".env.backup.$(date +%Y%m%d%H%M%S)"
        log_info "已备份现有 .env"
        GENERATE_ENV=true
    else
        log_info "保留现有 .env 配置"
    fi
else
    GENERATE_ENV=true
fi

if [ "$GENERATE_ENV" = true ]; then
    log_info "生成环境配置..."
    
    if [ ! -f ".env.example" ]; then
        log_error ".env.example 文件不存在"
        exit 1
    fi
    
    cp .env.example .env
    
    # 生成 FAMILY_VAULT_JWT_SECRET
    JWT_SECRET=$(openssl rand -hex 32)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/FAMILY_VAULT_JWT_SECRET=.*/FAMILY_VAULT_JWT_SECRET=$JWT_SECRET/" .env
        sed -i '' "s/<replace-with-your-secret>/$JWT_SECRET/" .env
    else
        sed -i "s/FAMILY_VAULT_JWT_SECRET=.*/FAMILY_VAULT_JWT_SECRET=$JWT_SECRET/" .env
        sed -i "s/<replace-with-your-secret>/$JWT_SECRET/" .env
    fi
    
    log_success "FAMILY_VAULT_JWT_SECRET 已自动生成"
fi

# 5. NAS 路径配置
log_info "NAS 路径配置"
echo ""
echo "请配置 NAS 或本地文档目录的挂载路径。"
echo "示例: /mnt/nas 或 /volume1/Family_Archives"
echo ""
read -p "NAS 源目录路径 (留空跳过): " NAS_PATH

if [ -n "$NAS_PATH" ]; then
    if [ -d "$NAS_PATH" ]; then
        log_success "NAS 目录存在: $NAS_PATH"
    else
        log_warn "NAS 目录不存在: $NAS_PATH"
        log_warn "请确保在 docker-compose.yml 中正确配置挂载后重启服务"
    fi
fi

# 6. 构建镜像
log_info "构建 Docker 镜像..."
docker compose build --no-cache
log_success "镜像构建完成"

# 7. 启动服务
log_info "启动服务..."
docker compose up -d

# 8. 等待服务就绪
log_info "等待服务启动..."
sleep 10

echo ""
echo "============================================"
echo "   Family Vault 安装完成!"
echo "============================================"
echo ""
echo "📍 访问地址:"
echo "   API:      http://localhost:18180"
echo "   前端:     http://localhost:18181"
echo "   Redis:    localhost:16379"
echo "   Qdrant:   http://localhost:16333"
echo ""
echo "📁 数据目录: $SCRIPT_DIR/data"
echo "⚙️  配置文件: $SCRIPT_DIR/.env"
echo ""
echo "🚀 后续操作:"
echo "   1. 访问前端页面创建管理员账户"
echo "   2. 在 Settings 中配置 NAS 源目录"
echo "   3. 配置 Gmail API (可选)"
echo ""
echo "📝 常用命令:"
echo "   查看日志:   docker compose logs -f"
echo "   重启服务:   docker compose restart"
echo "   停止服务:   docker compose down"
echo "   更新服务:   ./update.sh"
echo "   备份数据:   ./backup.sh"
echo ""

log_success "安装完成!"
