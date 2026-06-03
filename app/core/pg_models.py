"""
PostgreSQL SQLAlchemy ORM 模型定义
- tdx2db 公开 schema 表 (只读，K线数据)
- tradetoolkit schema 表 (app 业务数据)
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, Text,
    ForeignKey, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship

from app.core.config import settings

Base = declarative_base()
Base.metadata.schema = settings.PG_APP_SCHEMA


def utcnow():
    return datetime.now(timezone.utc)


# ============================================================
# tdx2db 公开 schema 表 (只读 - K线数据源)
# ============================================================

class DailyData(Base):
    """日K线数据 (tdx2db public.daily_data)"""
    __tablename__ = "daily_data"
    __table_args__ = {"schema": "public", "extend_existing": True}

    id = Column(Integer, primary_key=True)
    code = Column(String(10), index=True)
    market = Column(Integer)
    datetime = Column(DateTime, index=True)
    date = Column(DateTime, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma13 = Column(Float)
    ma21 = Column(Float)
    ma34 = Column(Float)
    ma55 = Column(Float)
    ma60 = Column(Float)
    ma89 = Column(Float)
    ma144 = Column(Float)
    ma233 = Column(Float)
    ma250 = Column(Float)


class Minute5Data(Base):
    """5分钟K线 (tdx2db public.minute5_data)"""
    __tablename__ = "minute5_data"
    __table_args__ = {"schema": "public", "extend_existing": True}

    id = Column(Integer, primary_key=True)
    code = Column(String(10), index=True, nullable=False)
    market = Column(Integer, nullable=False)
    datetime = Column(DateTime, index=True, nullable=False)
    date = Column(DateTime, index=True, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma13 = Column(Float)
    ma21 = Column(Float)
    ma34 = Column(Float)
    ma55 = Column(Float)
    ma60 = Column(Float)
    ma89 = Column(Float)
    ma144 = Column(Float)
    ma233 = Column(Float)
    ma250 = Column(Float)


class Minute15Data(Base):
    """15分钟K线"""
    __tablename__ = "minute15_data"
    __table_args__ = {"schema": "public", "extend_existing": True}

    id = Column(Integer, primary_key=True)
    code = Column(String(10), index=True, nullable=False)
    market = Column(Integer, nullable=False)
    datetime = Column(DateTime, index=True, nullable=False)
    date = Column(DateTime, index=True, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma13 = Column(Float)
    ma21 = Column(Float)
    ma34 = Column(Float)
    ma55 = Column(Float)
    ma60 = Column(Float)
    ma89 = Column(Float)
    ma144 = Column(Float)
    ma233 = Column(Float)
    ma250 = Column(Float)


class Minute30Data(Base):
    """30分钟K线"""
    __tablename__ = "minute30_data"
    __table_args__ = {"schema": "public", "extend_existing": True}

    id = Column(Integer, primary_key=True)
    code = Column(String(10), index=True, nullable=False)
    market = Column(Integer, nullable=False)
    datetime = Column(DateTime, index=True, nullable=False)
    date = Column(DateTime, index=True, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma13 = Column(Float)
    ma21 = Column(Float)
    ma34 = Column(Float)
    ma55 = Column(Float)
    ma60 = Column(Float)
    ma89 = Column(Float)
    ma144 = Column(Float)
    ma233 = Column(Float)
    ma250 = Column(Float)


class Minute60Data(Base):
    """60分钟K线"""
    __tablename__ = "minute60_data"
    __table_args__ = {"schema": "public", "extend_existing": True}

    id = Column(Integer, primary_key=True)
    code = Column(String(10), index=True, nullable=False)
    market = Column(Integer, nullable=False)
    datetime = Column(DateTime, index=True, nullable=False)
    date = Column(DateTime, index=True, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    amount = Column(Float, nullable=False)
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma13 = Column(Float)
    ma21 = Column(Float)
    ma34 = Column(Float)
    ma55 = Column(Float)
    ma60 = Column(Float)
    ma89 = Column(Float)
    ma144 = Column(Float)
    ma233 = Column(Float)
    ma250 = Column(Float)


class StockInfo(Base):
    """股票列表 (tdx2db public.stock_info)"""
    __tablename__ = "stock_info"
    __table_args__ = {"schema": "public", "extend_existing": True}

    id = Column(Integer, primary_key=True)
    code = Column(String(10), unique=True, index=True)
    name = Column(String(50))
    market = Column(Integer)


# ============================================================
# tradetoolkit schema 表 (app 业务数据)
# ============================================================

class StockBasicInfo(Base):
    """股票基础信息"""
    __tablename__ = "stock_basic_info"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, index=True)
    symbol = Column(String(16), index=True)
    name = Column(String(100))
    industry = Column(String(100))
    area = Column(String(50))
    market = Column(String(10))
    list_date = Column(String(10))
    source = Column(String(50), default="akshare")
    data_source = Column(String(50), default="akshare")

    # 市值信息
    total_mv = Column(Float)
    circ_mv = Column(Float)

    # 估值指标
    pe = Column(Float)
    pb = Column(Float)
    pe_ttm = Column(Float)
    pb_mrq = Column(Float)

    # 交易指标
    turnover_rate = Column(Float)
    volume_ratio = Column(Float)

    # 财务指标 (最新一期快照)
    roe = Column(Float)
    roa = Column(Float)
    netprofit_margin = Column(Float)
    gross_margin = Column(Float)

    # 扩展字段 (JSON存储)
    extra = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("code", "source", name="uq_stock_basic_code_source"),
        Index("ix_stock_basic_industry", "industry"),
        Index("ix_stock_basic_total_mv", "total_mv"),
        Index("ix_stock_basic_pe", "pe"),
        Index("ix_stock_basic_pb", "pb"),
        Index("ix_stock_basic_market", "market"),
    )


class MarketQuotes(Base):
    """实时行情数据"""
    __tablename__ = "market_quotes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, index=True)
    symbol = Column(String(16), index=True)
    name = Column(String(100))

    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    pre_close = Column(Float)
    pct_chg = Column(Float)
    change = Column(Float)
    volume = Column(Float)
    amount = Column(Float)

    trade_date = Column(String(10))
    data_source = Column(String(50), default="akshare")

    # 扩展数据
    extra = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("code", name="uq_market_quotes_code"),
        Index("ix_market_quotes_pct_chg", "pct_chg"),
        Index("ix_market_quotes_amount", "amount"),
        Index("ix_market_quotes_updated_at", "updated_at"),
    )


class StockFinancialData(Base):
    """股票财务数据"""
    __tablename__ = "stock_financial_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, index=True)
    symbol = Column(String(16))
    data_source = Column(String(50), default="akshare")

    report_period = Column(String(10))
    roe = Column(Float)
    roa = Column(Float)
    netprofit_margin = Column(Float)
    gross_margin = Column(Float)
    revenue = Column(Float)
    net_profit = Column(Float)
    total_assets = Column(Float)
    total_equity = Column(Float)
    eps = Column(Float)
    bps = Column(Float)

    extra = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_financial_code_period", "code", "report_period"),
        Index("ix_financial_source", "data_source"),
    )


class StockNewsData(Base):
    """股票新闻数据"""
    __tablename__ = "stock_news_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), index=True)
    symbol = Column(String(16), index=True)
    title = Column(Text)
    content = Column(Text)
    source = Column(String(100))
    source_url = Column(Text)
    publish_time = Column(DateTime)
    data_source = Column(String(50), default="akshare")

    extra = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_news_code_time", "code", "publish_time"),
        Index("ix_news_source", "data_source"),
    )


class User(Base):
    """用户"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(120), unique=True, index=True)
    hashed_password = Column(String(255))
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    daily_quota = Column(Integer, default=1000)
    concurrent_limit = Column(Integer, default=3)

    preferences = Column(JSONB)
    favorite_stocks = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    last_login = Column(DateTime)


class UserFavorite(Base):
    """用户自选股"""
    __tablename__ = "user_favorites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.users.id"), nullable=False, index=True)
    stock_code = Column(String(16), nullable=False)
    name = Column(String(100))
    market = Column(String(10))
    tags = Column(JSONB)
    alerts = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "stock_code", name="uq_user_stock_favorite"),
    )


class LLMProvider(Base):
    """LLM 厂家配置"""
    __tablename__ = "llm_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False, index=True)
    display_name = Column(String(100))
    provider_type = Column(String(50))
    api_key = Column(String(500))
    api_base = Column(String(500))
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=0)

    config = Column(JSONB)
    models = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ModelCatalog(Base):
    """模型目录"""
    __tablename__ = "model_catalog"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.llm_providers.id"), index=True)
    model_name = Column(String(100), nullable=False)
    display_name = Column(String(200))
    capability_level = Column(Integer, default=1)
    suitable_roles = Column(JSONB)
    features = Column(JSONB)
    input_price_per_1k = Column(Float, default=0.0)
    output_price_per_1k = Column(Float, default=0.0)
    currency = Column(String(10), default="CNY")
    is_active = Column(Boolean, default=True)

    config = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)


class SystemConfig(Base):
    """系统配置 (JSONB 存储灵活配置)"""
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, default=1, index=True)
    is_active = Column(Boolean, default=True, index=True)

    system_settings = Column(JSONB)
    llm_configs = Column(JSONB)
    data_source_configs = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_system_config_active_version", "is_active", "version"),
    )


class OperationLog(Base):
    """操作日志"""
    __tablename__ = "operation_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, index=True)
    username = Column(String(50))
    action = Column(String(100), index=True)
    resource = Column(String(200))
    details = Column(JSONB)
    ip_address = Column(String(50))

    created_at = Column(DateTime, default=utcnow, index=True)


class AnalysisTask(Base):
    """分析任务"""
    __tablename__ = "analysis_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.users.id"), index=True)
    symbol = Column(String(16), nullable=False, index=True)
    status = Column(String(20), default="pending", index=True)

    parameters = Column(JSONB)
    result = Column(JSONB)

    progress = Column(Float, default=0.0)
    retry_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_analysis_user_status", "user_id", "status"),
        Index("ix_analysis_symbol", "symbol"),
    )


class AnalysisBatch(Base):
    """分析批次"""
    __tablename__ = "analysis_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.users.id"), index=True)
    status = Column(String(20), default="pending")

    tasks = Column(JSONB)
    result = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class PatternScreeningTask(Base):
    """形态筛选任务"""
    __tablename__ = "pattern_screening_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), unique=True, nullable=False, index=True)
    pattern_type = Column(String(50), nullable=False, index=True)
    status = Column(String(20), default="pending", index=True)

    parameters = Column(JSONB)
    result = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class StrategyDefinition(Base):
    """策略定义"""
    __tablename__ = "strategy_definitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text)
    status = Column(String(20), default="draft", index=True)

    config = Column(JSONB)  # trend/quality/buy/sell/position rules

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class StrategyRun(Base):
    """策略运行记录"""
    __tablename__ = "strategy_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.strategy_definitions.id"), nullable=False, index=True)
    status = Column(String(20), default="running", index=True)

    events = Column(JSONB)
    signals = Column(JSONB)
    result = Column(JSONB)

    started_at = Column(DateTime, default=utcnow)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)


class StrategyPool(Base):
    """策略选股池"""
    __tablename__ = "strategy_pool"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.strategy_definitions.id"), index=True)
    stock_code = Column(String(16), nullable=False, index=True)
    stock_name = Column(String(100))
    status = Column(String(20), default="active")

    score = Column(Float)
    reason = Column(Text)
    details = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("strategy_id", "stock_code", name="uq_strategy_pool_stock"),
    )


class StrategyBacktest(Base):
    """策略回测"""
    __tablename__ = "strategy_backtests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.strategy_definitions.id"), index=True)
    status = Column(String(20), default="completed")

    parameters = Column(JSONB)
    result = Column(JSONB)

    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)


class TokenUsage(Base):
    """Token 使用统计"""
    __tablename__ = "token_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, index=True)
    provider = Column(String(50), index=True)
    model = Column(String(100))
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)
    cost = Column(Float, default=0.0)
    request_type = Column(String(50))

    metadata_ = Column("metadata", JSONB)

    created_at = Column(DateTime, default=utcnow, index=True)


class Notification(Base):
    """通知"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.users.id"), nullable=False, index=True)
    type = Column(String(50), index=True)  # analysis/alert/system
    title = Column(String(200))
    message = Column(Text)
    status = Column(String(20), default="unread")  # unread/read

    data = Column(JSONB)

    created_at = Column(DateTime, default=utcnow, index=True)


class StockTag(Base):
    """股票标签"""
    __tablename__ = "stock_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey(f"{settings.PG_APP_SCHEMA}.users.id"), index=True)
    stock_code = Column(String(16), nullable=False, index=True)
    tag_name = Column(String(50), nullable=False, index=True)

    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "stock_code", "tag_name", name="uq_stock_tag"),
    )


class QuotesIngestionStatus(Base):
    """行情入库状态"""
    __tablename__ = "quotes_ingestion_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    last_sync_time = Column(DateTime)
    last_sync_time_iso = Column(String(30))
    interval_seconds = Column(Integer, default=360)
    status = Column(String(20), default="idle")
    data_source = Column(String(50))
    records_count = Column(Integer, default=0)
    error_message = Column(Text)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class SyncStatus(Base):
    """同步状态记录"""
    __tablename__ = "sync_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job = Column(String(100), index=True)
    data_type = Column(String(50))
    status = Column(String(30), default="running")
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    total_stocks = Column(Integer, default=0)
    processed_stocks = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    extra = Column(JSONB)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_sync_status_job", "job"),
        Index("ix_sync_status_started", "started_at"),
    )


class MarketCategory(Base):
    """市场分类"""
    __tablename__ = "market_categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100))
    markets = Column(JSONB)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=utcnow)


class DataSourceGrouping(Base):
    """数据源分组"""
    __tablename__ = "data_source_groupings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100))
    data_sources = Column(JSONB)
    market_categories = Column(JSONB)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class DatabaseBackup(Base):
    """数据库备份记录"""
    __tablename__ = "db_backups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(500), nullable=False)
    size_bytes = Column(Integer)
    status = Column(String(20), default="completed")
    error_message = Column(Text)

    created_at = Column(DateTime, default=utcnow, index=True)
