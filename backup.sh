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
DB_BACKUP_ARTIFACT=""

resolve_database_url() {
    local value="${DATABASE_URL:-}"
    if [ -z "$value" ] && [ -n "${FAMILY_VAULT_DATABASE_URL:-}" ]; then
        value="$FAMILY_VAULT_DATABASE_URL"
    fi
    if [ -z "$value" ] && [ -f ".env" ]; then
        value=$(grep -E '^[[:space:]]*FAMILY_VAULT_DATABASE_URL=' .env | tail -n 1 | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    fi
    echo "$value"
}

detect_db_type() {
    local db_url="$1"
    if [[ "$db_url" == postgresql://* ]] || [[ "$db_url" == postgresql+*://* ]] || [[ "$db_url" == postgres://* ]]; then
        echo "postgres"
        return
    fi
    if [[ "$db_url" == sqlite://* ]] || [[ "$db_url" == sqlite:* ]]; then
        echo "sqlite"
        return
    fi
    echo "unknown"
}

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

DB_URL="$(resolve_database_url)"
DB_TYPE="$(detect_db_type "$DB_URL")"
log_info "检测数据库类型: $DB_TYPE"

# 创建备份目录
mkdir -p "$BACKUP_DIR"
TEMP_DIR="$BACKUP_DIR/$BACKUP_NAME"
mkdir -p "$TEMP_DIR"

# 1. 备份数据库
log_info "备份数据库..."
if [ "$DB_TYPE" = "postgres" ]; then
    if ! command -v pg_dump >/dev/null 2>&1; then
        log_error "检测到 PostgreSQL，但未找到 pg_dump 命令"
        exit 1
    fi
    DB_BACKUP_ARTIFACT="$TEMP_DIR/family_vault.pg.dump"
    pg_dump --format=custom --no-owner --no-privileges --file="$DB_BACKUP_ARTIFACT" "$DB_URL"
    log_success "PostgreSQL 已备份: $DB_BACKUP_ARTIFACT"
elif [ -f "data/family_vault.db" ]; then
    DB_BACKUP_ARTIFACT="$TEMP_DIR/family_vault.db"
    if command -v sqlite3 >/dev/null 2>&1; then
        # WAL 模式下先 checkpoint，再使用 SQLite 原生在线备份保证一致性。
        sqlite3 data/family_vault.db "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null
        sqlite3 data/family_vault.db ".backup \"$DB_BACKUP_ARTIFACT\""
        log_success "SQLite 数据库已备份（checkpoint + .backup）"
    else
        log_warn "未找到 sqlite3，回退为文件复制（可能受 WAL 影响）"
        cp data/family_vault.db "$DB_BACKUP_ARTIFACT"
        [ -f data/family_vault.db-wal ] && cp data/family_vault.db-wal "$TEMP_DIR/family_vault.db-wal"
        [ -f data/family_vault.db-shm ] && cp data/family_vault.db-shm "$TEMP_DIR/family_vault.db-shm"
        log_success "SQLite 数据库文件已复制（含 WAL/SHM）"
    fi
else
    log_warn "未检测到可备份数据库（SQLite 文件不存在，且未配置 PostgreSQL URL）"
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
echo "   # SQLite: cp ${BACKUP_NAME}/family_vault.db data/"
echo "   # PostgreSQL: pg_restore --clean --if-exists -d \"\$DATABASE_URL\" ${BACKUP_NAME}/family_vault.pg.dump"
echo "   cp ${BACKUP_NAME}/.env ."
echo ""

log_success "备份完成!"
