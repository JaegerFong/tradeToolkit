#!/usr/bin/env bash

# 本地开发环境运行
# 1. 激活本地环境
# conda create -n tradeToolkit python=3.10
# pip install -e .
# 2. PostgreSQL (tdx2db K线 + 业务数据) 和 Redis
# PG 连接信息在 .env 中配置 (PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DATABASE)
# Redis 可用 docker compose -f docker-compose.yml up -d redis 启动
# 3. 创建 .env 文件
# 4. 前端依赖
# cd frontend && npm install
# 5. 启动后端
# ./scripts/dev.sh backend
# 6. 启动前端
# cd frontend && npm run dev -- --host 0.0.0.0 --port 3000


# 本地开发启动脚本
# 前提: PostgreSQL (参照 .env 配置) 和 Redis 已可用
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

    # 检查 PostgreSQL
    if "$VENV_PYTHON" -c "
import asyncpg, os, sys
from dotenv import load_dotenv
load_dotenv()
try:
    conn = __import__('asyncio').run(asyncpg.connect(
        host=os.getenv('PG_HOST','localhost'),
        port=int(os.getenv('PG_PORT','5432')),
        user=os.getenv('PG_USER',''),
        password=os.getenv('PG_PASSWORD',''),
        database=os.getenv('PG_DATABASE','tradingagents'),
        timeout=5))
    ver = __import__('asyncio').run(conn.fetchval('SELECT version()'))
    conn.close()
    print(f'OK:{ver.split(\",\")[0]}')
except Exception as e:
    print(f'FAIL:{e}')
    sys.exit(1)
" 2>/dev/null; then
        log "PostgreSQL 连接正常"
    else
        warn "PostgreSQL 连接失败，请确认 PG 已启动 (参照 .env 中 PG_HOST/PG_PORT)"
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

前提: PostgreSQL (参照 .env 配置) 和 Redis (localhost:6379) 需可用

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
