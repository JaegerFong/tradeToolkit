#!/usr/bin/env bash
# 本地开发启动脚本
# 前提: MongoDB 和 Redis 已在本地运行
# 用法:
#   ./scripts/dev.sh backend    # 仅启动后端 http://localhost:8000
#   ./scripts/dev.sh frontend   # 仅启动前端 http://localhost:3000
#   ./scripts/dev.sh all        # 启动前后端 (后端后台 + 前端前台)

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[DEV]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()  { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

check_prerequisites() {
    # 检查 Python venv
    if [[ ! -x "$VENV_PYTHON" ]]; then
        die "虚拟环境未找到，请先运行: python3 -m venv .venv && .venv/bin/pip install -e ."
    fi

    # 检查 MongoDB
    if ! command -v mongosh &>/dev/null && ! command -v mongo &>/dev/null; then
        warn "未检测到 mongo CLI，跳过 MongoDB 连接检查"
    else
        local mongo_cmd="mongosh"
        command -v $mongo_cmd &>/dev/null || mongo_cmd="mongo"
        if ! $mongo_cmd --quiet --eval "db.runCommand('ping').ok" 2>/dev/null | grep -q "1"; then
            warn "MongoDB 连接失败，请确认 MongoDB 已启动 (localhost:27017)"
        else
            log "MongoDB 连接正常"
        fi
    fi

    # 检查 Redis
    if command -v redis-cli &>/dev/null; then
        if redis-cli ping &>/dev/null; then
            log "Redis 连接正常"
        else
            warn "Redis 连接失败，请确认 Redis 已启动 (localhost:6379)"
        fi
    else
        warn "未检测到 redis-cli，跳过 Redis 连接检查"
    fi
}

start_backend() {
    log "启动后端服务 (uvicorn --reload)..."
    cd "$ROOT_DIR"
    "$VENV_PYTHON" -m uvicorn app.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --reload \
        --reload-dir app \
        --reload-dir tradingagents
}

start_frontend() {
    log "启动前端服务 (Vite dev)..."
    cd "${ROOT_DIR}/frontend"

    if [[ ! -d node_modules ]]; then
        warn "node_modules 不存在，正在安装依赖..."
        if command -v yarn &>/dev/null; then
            yarn install --frozen-lockfile
        else
            npm install
        fi
    fi

    if command -v yarn &>/dev/null; then
        exec yarn dev --host 0.0.0.0 --port 3000
    else
        exec npm run dev -- --host 0.0.0.0 --port 3000
    fi
}

usage() {
    cat <<EOF
用法: $0 <command>

commands:
  backend    启动后端 (FastAPI + uvicorn --reload) → http://localhost:8000
  frontend   启动前端 (Vite dev server) → http://localhost:3000
  all        同时启动前后端 (后端后台运行，前端前台运行)

前提: MongoDB (:27017) 和 Redis (:6379) 需已启动

示例:
  $0 backend          # 终端1: 启动后端
  $0 frontend         # 终端2: 启动前端
  $0 all              # 单终端启动全部
EOF
}

case "${1:-}" in
    backend)
        check_prerequisites
        start_backend
        ;;
    frontend)
        start_frontend
        ;;
    all)
        check_prerequisites
        log "后台启动后端..."
        "$VENV_PYTHON" -m uvicorn app.main:app \
            --host 0.0.0.0 --port 8000 \
            --reload --reload-dir app --reload-dir tradingagents &
        BACKEND_PID=$!
        trap "kill $BACKEND_PID 2>/dev/null; exit" INT TERM
        sleep 2
        log "后端已启动 (PID: $BACKEND_PID)"
        start_frontend
        ;;
    -h|--help|help|"")
        usage
        ;;
    *)
        die "未知命令: $1"
        ;;
esac
