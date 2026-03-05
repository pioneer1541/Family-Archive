#!/bin/bash
set -e

# ============================================
# Family Vault 备份脚本
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

# 配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
BACKUP_DIR="$SCRIPT_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="family_vault_backup_$TIMESTAMP"
KEEP_BACKUPS=10  # 保留最近 N 个备份

# 参数解析
BACKUP_TYPE="full"  # full, quick, data-only
while [[ $# -gt 0 ]]; do
    case $1 in
        --quick|-q)
            BACKUP_TYPE="quick"
            shift
            ;;
        --data-only|-d)
            BACKUP_TYPE="data-only"
            shift
            ;;
        --keep|-k)
            KEEP_BACKUPS="$2"
            shift 2
            ;;
        --help|-h)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --quick, -q       快速备份 (仅数据库和配置)"
            echo "  --data-only, -d   仅备份数据目录"
            echo "  --keep N, -k N    保留最近 N 个备份 (默认 10)"
            echo "  --help, -h        显示帮助信息"
            exit 0
            ;;
        *)
            log_error "未知参数: $1"
            exit 1
            ;;
    esac
done

echo ""
echo "============================================"
echo "   Family Vault 备份脚本"
echo "============================================"
echo "备份类型: $BACKUP_TYPE"
echo ""

# 创建备份目录
mkdir -p "$BACKUP_DIR"
TEMP_DIR="$BACKUP_DIR/$BACKUP_NAME"
mkdir -p "$TEMP_DIR"

# 1. 备份数据库
log_info "备份数据库..."
if [ -f "data/family_vault.db" ]; then
    cp data/family_vault.db "$TEMP_DIR/family_vault.db"
    log_success "数据库已备份"
else
    log_warn "数据库文件不存在"
fi

# 2. 备份配置
log_info "备份配置文件..."
if [ -f ".env" ]; then
    cp .env "$TEMP_DIR/.env"
    log_success ".env 已备份"
fi

if [ -f "docker-compose.yml" ]; then
    cp docker-compose.yml "$TEMP_DIR/docker-compose.yml"
    log_success "docker-compose.yml 已备份"
fi

# 3. 根据备份类型执行不同操作
if [ "$BACKUP_TYPE" = "full" ]; then
    log_info "执行完整备份..."
    
    # 备份 Qdrant 数据
    if [ -d "data/qdrant" ]; then
        log_info "备份 Qdrant 向量数据..."
        cp -r data/qdrant "$TEMP_DIR/"
        log_success "Qdrant 数据已备份"
    fi
    
    # 备份邮件附件
    if [ -d "data/mail_attachments" ]; then
        log_info "备份邮件附件..."
        cp -r data/mail_attachments "$TEMP_DIR/"
        log_success "邮件附件已备份"
    fi
    
    # 备份 secrets (限制权限)
    if [ -d "secrets" ]; then
        log_info "备份 secrets..."
        cp -r secrets "$TEMP_DIR/"
        chmod -R 600 "$TEMP_DIR/secrets" 2>/dev/null || true
        log_success "secrets 已备份 (权限已限制)"
    fi
    
elif [ "$BACKUP_TYPE" = "quick" ]; then
    log_info "执行快速备份 (仅数据库和配置)"
    # 已经备份了数据库和配置
fi

# 4. 压缩备份
log_info "压缩备份文件..."
cd "$BACKUP_DIR"
tar -czf "${BACKUP_NAME}.tar.gz" "$BACKUP_NAME"
rm -rf "$BACKUP_NAME"

# 设置备份文件权限 (包含敏感信息)
chmod 600 "${BACKUP_NAME}.tar.gz"

BACKUP_SIZE=$(du -h "${BACKUP_NAME}.tar.gz" | cut -f1)
log_success "备份完成: ${BACKUP_NAME}.tar.gz ($BACKUP_SIZE)"

# 5. 清理旧备份
log_info "清理旧备份 (保留最近 $KEEP_BACKUPS 个)..."
ls -t family_vault_backup_*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm
REMAINING=$(ls -1 family_vault_backup_*.tar.gz 2>/dev/null | wc -l | tr -d ' ')
log_success "当前保留 $REMAINING 个备份"

# 6. 显示备份列表
echo ""
echo "============================================"
echo "   备份列表"
echo "============================================"
ls -lth family_vault_backup_*.tar.gz 2>/dev/null | head -10

echo ""
echo "备份目录: $BACKUP_DIR"
echo ""

# 7. 恢复命令提示
echo "📝 恢复命令:"
echo "   tar -xzf ${BACKUP_NAME}.tar.gz"
echo "   cp ${BACKUP_NAME}/family_vault.db data/"
echo "   cp ${BACKUP_NAME}/.env ."
echo ""

log_success "备份完成!"
