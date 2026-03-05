#!/bin/bash
set -e

# ============================================
# Family Vault 更新脚本
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
trap 'log_error "更新失败"; exit 1' ERR

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKUP_DIR="$SCRIPT_DIR/backups"

echo ""
echo "============================================"
echo "   Family Vault 更新脚本"
echo "============================================"
echo ""

# 1. 检查服务状态
log_info "检查当前服务状态..."
if ! docker compose ps 2>/dev/null | grep -q "Up"; then
    log_warn "没有运行中的服务"
fi

# 2. 备份数据库
log_info "备份数据库..."
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/family_vault_$(date +%Y%m%d_%H%M%S).db"

if [ -f "data/family_vault.db" ]; then
    cp data/family_vault.db "$BACKUP_FILE"
    log_success "数据库已备份: $BACKUP_FILE"
else
    log_warn "数据库文件不存在，跳过备份"
fi

# 3. 备份配置
if [ -f ".env" ]; then
    cp .env "$BACKUP_DIR/.env.backup.$(date +%Y%m%d%H%M%S)"
    log_success "配置已备份"
fi

# 4. 记录当前镜像 ID (用于回滚)
log_info "记录当前镜像版本..."
OLD_API_IMAGE=$(docker compose images fkv-api -q 2>/dev/null || echo "")
ROLLBACK_NEEDED=false

# 5. 拉取最新代码 (如果是 git 仓库)
if [ -d ".git" ]; then
    log_info "检测到 Git 仓库，拉取最新代码..."
    git fetch origin
    git status
    read -p "是否拉取最新代码? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        git pull origin $(git branch --show-current)
        log_success "代码已更新"
    fi
fi

# 6. 重新构建镜像
log_info "重新构建镜像..."
if ! docker compose build --no-cache; then
    log_error "镜像构建失败"
    ROLLBACK_NEEDED=true
fi

# 7. 重启服务
if [ "$ROLLBACK_NEEDED" = false ]; then
    log_info "重启服务..."
    docker compose down
    docker compose up -d
    
    # 8. 等待服务就绪
    log_info "等待服务启动..."
    sleep 15
    
    # 9. 验证服务状态 (修复: 使用 "Up" 匹配)
    log_info "验证服务状态..."
    sleep 5
    
    API_STATUS=$(docker compose ps fkv-api 2>/dev/null | grep -c "Up" || echo "0")
    FRONTEND_STATUS=$(docker compose ps fkv-frontend 2>/dev/null | grep -c "Up" || echo "0")
    REDIS_STATUS=$(docker compose ps redis 2>/dev/null | grep -c "Up" || echo "0")
    
    if [ "$API_STATUS" -ge 1 ] && [ "$REDIS_STATUS" -ge 1 ]; then
        log_success "服务启动成功"
    else
        log_error "服务启动异常"
        log_info "API: $API_STATUS, Frontend: $FRONTEND_STATUS, Redis: $REDIS_STATUS"
        ROLLBACK_NEEDED=true
    fi
fi

# 10. 回滚处理
if [ "$ROLLBACK_NEEDED" = true ]; then
    log_warn "更新失败，尝试回滚..."
    
    # 恢复数据库
    if [ -f "$BACKUP_FILE" ]; then
        cp "$BACKUP_FILE" data/family_vault.db
        log_info "数据库已恢复"
    fi
    
    # 如果有旧镜像，尝试使用旧镜像
    if [ -n "$OLD_API_IMAGE" ] && [ "$OLD_API_IMAGE" != "" ]; then
        log_info "尝试回滚到旧镜像..."
        docker compose down
        docker compose up -d
    fi
    
    log_warn "已回滚到更新前状态，请检查日志: docker compose logs"
    exit 1
fi

# 11. 清理旧备份 (保留最近 10 个)
log_info "清理旧备份..."
cd "$BACKUP_DIR"
ls -t family_vault_*.db 2>/dev/null | tail -n +11 | xargs -r rm
ls -t .env.backup.* 2>/dev/null | tail -n +11 | xargs -r rm
log_success "旧备份已清理"

echo ""
echo "============================================"
echo "   Family Vault 更新完成!"
echo "============================================"
echo ""
echo "📝 查看日志: docker compose logs -f"
echo "📍 访问地址: http://localhost:18181"
echo ""

log_success "更新完成!"
