# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

### Local Development

```bash
# First-time setup
./scripts/local-dev.sh env           # Generate .env
./scripts/local-dev.sh deps          # Start MongoDB + Redis in Docker
./scripts/local-dev.sh install-backend  # Create .venv and pip install -e .
./scripts/local-dev.sh install-frontend # Install frontend dependencies
./scripts/local-dev.sh migrate       # Run database migration

# Start services
./scripts/local-dev.sh backend       # FastAPI on :8000 (with reload)
./scripts/local-dev.sh frontend      # Vite on :3000

# Data sync (all require deps running)
./scripts/local-dev.sh data-status         # Check DB state
./scripts/local-dev.sh sync-basics         # Sync stock basic info only
./scripts/local-dev.sh sync-strategy 365   # Basics + daily for N days
./scripts/local-dev.sh sync-strategy-fast 365 500  # Lightweight: basics + N stocks daily
./scripts/local-dev.sh sync-a-share-2y-all 730  # Full A-share 2-year sync
```

### Direct CLI

```bash
# Test akshare connection
python cli/akshare_init.py --test-connection

# Sync specific items
python cli/akshare_init.py --full --force --sync-items basic_info,historical --historical-days 365
# Available sync-items: basic_info,historical,weekly,monthly,financial,quotes,news

# Check-only mode
python cli/akshare_init.py --check-only
```

### Docker Deployment

```bash
docker compose up -d                    # Full stack
docker compose -f docker-compose.local-dev.yml up -d  # MongoDB + Redis only
```

### Testing

```bash
pytest tests/                           # All tests
pytest tests/test_akshare_api.py        # Single test file
pytest tests/ -k "akshare"             # Filter by name
```

## Architecture Overview

### Backend (`app/`) — FastAPI + MongoDB + Redis

**Data flow**: FastAPI routers → services → workers/sync services → MongoDB

Two parallel data-source systems coexist:

| System | Location | Usage |
|--------|----------|-------|
| **Provider system** (newer) | `tradingagents/dataflows/providers/` | Sync workers, trading agents |
| **Adapter system** (legacy) | `app/services/data_sources/` | Multi-source fallback, API endpoints |

The provider system has a dedicated network layer at `tradingagents/dataflows/providers/china/akshare_network.py` that monkey-patches `requests.get` globally for proxy rotation, rate limiting, and TLS fingerprint simulation. This patch must be applied before `import akshare`. Entry points (`app/main.py` lifespan, `cli/akshare_init.py`) call `init_akshare_network()` to ensure the patch is active.

**Key services**: `simple_analysis_service.py` (139KB) is the core analysis engine. Pattern screening has its own service + LLM agent. Config is managed by `config_service.py` (205KB) backed by MongoDB `system_configs` collection.

**Database**: MongoDB collections include `stock_basic_info`, `market_quotes`, `stock_daily_quotes`, `stock_financial_data`, `news_data`, `users`, `system_configs`, `datasource_groupings`, `sync_status`.

**Scheduler**: APScheduler (`AsyncIOScheduler`) registered in `app/main.py` lifespan. Tasks include data sync (Tushare/AKShare/BaoStock), quotes ingestion, and strategy reconciliation. All can be toggled via env vars (e.g., `AKSHARE_BASIC_INFO_SYNC_ENABLED=false`).

### Frontend (`frontend/`) — Vue 3 + Vite + Element Plus + TypeScript

SPA with Pinia stores (`auth`, `app`, `notifications`). API calls go through `frontend/src/api/request.ts` (axios instance with interceptors). Views organized by feature: `PatternScreening/`, `Strategies/`, `Screening/`, `Analysis/`, `Stocks/`, `System/`, etc.

### Trading Agents (`tradingagents/`) — Multi-Agent AI System

Uses LangGraph for multi-agent workflows. `dataflows/` contains the data provider layer, caching, and technical analysis. `dataflows/data_source_manager.py` (113KB) orchestrates multi-source data with priority-based fallback (Tushare > AKShare > BaoStock).

### Streamlit Web (`web/`)

Separate Streamlit app for demo/testing, launched via `web/run_web.py`.

## Environment Configuration

`.env.example` is the authoritative template (600+ lines). Notable sections:

- **AKShare network layer**: `AKSHARE_PROXY_MODE=strong` (off/basic/strong), `AKSHARE_PROXY_API_URL` for dynamic proxy API, `AKSHARE_PROXIES` for static proxy list, `AKSHARE_USE_CURL_CFFI=true` for TLS fingerprint simulation, `AKSHARE_MIN_REQUEST_INTERVAL` for rate limiting
- **Data source toggles**: `TUSHARE_UNIFIED_ENABLED`, `AKSHARE_UNIFIED_ENABLED`, `BAOSTOCK_UNIFIED_ENABLED` control which sources run scheduled syncs
- **MongoDB scope**: `MONGODB_DATABASE_SCOPE=auto` appends version/instance tags for dev isolation; use `explicit` for production
- **NO_PROXY** must include `eastmoney.com,push2.eastmoney.com` for AKShare to work correctly through proxies

## Key Patterns

- **Global singletons**: `get_akshare_provider()`, `get_akshare_sync_service()`, `get_akshare_init_service()` — factory functions that create once and cache
- **Async everywhere**: Data fetching uses `asyncio.to_thread()` to wrap synchronous akshare/tushare calls
- **Monkey-patching**: AKShare's `requests.get` is patched at startup for proxy rotation. The patch is idempotent (`_akshare_headers_patched` guard)
- **Multi-source fallback**: `DataSourceManager` tries adapters in priority order; methods named `*_with_fallback` cascade through sources
- **MongoDB upsert**: All sync services use upsert semantics to allow re-running without duplicates

## Commit Convention

Commit messages follow `type(scope): description` format (e.g., `feat(akshare): add proxy pool rotation`).
