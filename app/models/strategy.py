from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.user import PyObjectId
from app.utils.timezone import now_tz


class StrategyStatus(str, Enum):
    DRAFT = "draft"
    ENABLED = "enabled"
    DISABLED = "disabled"


class StrategyValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"


class StrategyRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DATA_INCOMPLETE = "data_incomplete"


class StrategyPoolStatus(str, Enum):
    CANDIDATE = "candidate"
    WATCHING = "watching"
    PLANNED_BUY = "planned_buy"
    HOLDING_MANUAL = "holding_manual"
    EXIT_WATCH = "exit_watch"
    REMOVED = "removed"


class StrategySchedule(BaseModel):
    enabled: bool = False
    cron: str = Field("30 18 * * 1-5", description="APScheduler crontab expression")


class StrategyConfig(BaseModel):
    name: str = "强趋势股量化交易系统"
    market: Literal["CN"] = "CN"
    min_listed_days: int = 60
    initial_filter: Dict[str, Any] = Field(default_factory=dict)
    trend_confirmation: Dict[str, Any] = Field(default_factory=dict)
    quality_score: Dict[str, Any] = Field(default_factory=dict)
    buy_rules: Dict[str, Any] = Field(default_factory=dict)
    sell_rules: Dict[str, Any] = Field(default_factory=dict)
    position_rules: Dict[str, Any] = Field(default_factory=dict)
    backtest: Dict[str, Any] = Field(default_factory=dict)
    optional_enhancements: List[str] = Field(default_factory=list)


class StrategyParseResult(BaseModel):
    status: StrategyValidationStatus
    config: StrategyConfig
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    missing_sections: List[str] = Field(default_factory=list)


class StrategyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    markdown: str = Field(..., min_length=20)
    enabled: bool = False
    schedule: StrategySchedule = Field(default_factory=StrategySchedule)


class StrategyUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    markdown: Optional[str] = Field(None, min_length=20)
    enabled: Optional[bool] = None
    schedule: Optional[StrategySchedule] = None


class StrategyDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    strategy_id: str
    user_id: str
    name: str
    markdown: str
    version: int = 1
    status: StrategyStatus = StrategyStatus.DRAFT
    schedule: StrategySchedule = Field(default_factory=StrategySchedule)
    parse_result: StrategyParseResult
    created_at: datetime = Field(default_factory=now_tz)
    updated_at: datetime = Field(default_factory=now_tz)


class StrategySummary(BaseModel):
    strategy_id: str
    name: str
    version: int
    status: StrategyStatus
    schedule: StrategySchedule
    validation_status: StrategyValidationStatus
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class StrategyDetail(StrategySummary):
    markdown: str
    config: StrategyConfig


class StrategyProgress(BaseModel):
    percent: int = Field(0, ge=0, le=100)
    step: str = "init"
    message: str = "任务已创建"


class StrategyRunStats(BaseModel):
    total_scanned: int = 0
    initial_candidates: int = 0
    trend_confirmed: int = 0
    quality_candidates: int = 0
    selected_count: int = 0
    removed_count: int = 0


class StrategyRun(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    run_id: str
    strategy_id: str
    user_id: str
    strategy_version: int
    status: StrategyRunStatus = StrategyRunStatus.QUEUED
    run_type: Literal["manual", "scheduled"] = "manual"
    as_of_date: Optional[str] = None
    created_at: datetime = Field(default_factory=now_tz)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: StrategyProgress = Field(default_factory=StrategyProgress)
    stats: StrategyRunStats = Field(default_factory=StrategyRunStats)
    summary: Optional[str] = None
    daily_review: Optional[str] = None
    next_day_plan: Optional[str] = None
    error: Optional[str] = None
    cancel_requested: bool = False


class StrategyRunEvent(BaseModel):
    run_id: str
    strategy_id: str
    timestamp: datetime = Field(default_factory=now_tz)
    step: str
    title: str
    message: str
    progress: int = Field(0, ge=0, le=100)
    data: Dict[str, Any] = Field(default_factory=dict)


class StrategySignal(BaseModel):
    signal_type: str
    name: str
    reason: str
    confidence: str = "medium"
    suggested_position: Optional[str] = None


class StrategyRunResult(BaseModel):
    code: str
    name: str
    signal_date: str
    status: StrategyPoolStatus
    total_score: float = 0
    trend_score: float = 0
    quality_score: float = 0
    buy_score: float = 0
    enhancement_score: float = 0
    close: float = 0
    pct_chg_5d: float = 0
    quality_signals: List[str] = Field(default_factory=list)
    buy_signals: List[StrategySignal] = Field(default_factory=list)
    sell_signals: List[StrategySignal] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    entry_reason: str = ""
    review: str = ""
    next_day_plan: str = ""
    stop_loss: Optional[float] = None
    invalid_conditions: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)


class StrategyRunResponse(BaseModel):
    run_id: str
    strategy_id: str
    status: StrategyRunStatus
    run_type: str
    as_of_date: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    progress: StrategyProgress
    stats: StrategyRunStats
    summary: Optional[str] = None
    daily_review: Optional[str] = None
    next_day_plan: Optional[str] = None
    error: Optional[str] = None


class StrategyRunResultList(BaseModel):
    total: int
    items: List[StrategyRunResult]


class StrategyPoolItem(BaseModel):
    strategy_id: str
    code: str
    name: str
    status: StrategyPoolStatus
    entered_at: datetime
    entry_date: str
    entry_reason: str
    last_signal_date: str
    last_score: float = 0
    tracking_days: int = 0
    removed_at: Optional[datetime] = None
    removed_reason: Optional[str] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)


class StrategyBacktestCreateRequest(BaseModel):
    start_date: str
    end_date: str
    max_symbols: Optional[int] = Field(None, ge=1, le=10000)
    holding_days: int = Field(3, ge=1, le=30)


class StrategyBacktestMetrics(BaseModel):
    total_signals: int = 0
    wins: int = 0
    win_rate: float = 0
    avg_return: float = 0
    max_favorable_return: float = 0
    max_adverse_return: float = 0
    by_signal_type: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class StrategyBacktest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    backtest_id: str
    strategy_id: str
    user_id: str
    strategy_version: int
    request: StrategyBacktestCreateRequest
    status: StrategyRunStatus = StrategyRunStatus.QUEUED
    created_at: datetime = Field(default_factory=now_tz)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: StrategyProgress = Field(default_factory=StrategyProgress)
    metrics: StrategyBacktestMetrics = Field(default_factory=StrategyBacktestMetrics)
    summary: Optional[str] = None
    error: Optional[str] = None


class StrategyBacktestResponse(BaseModel):
    backtest_id: str
    strategy_id: str
    status: StrategyRunStatus
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    progress: StrategyProgress
    metrics: StrategyBacktestMetrics
    summary: Optional[str] = None
    error: Optional[str] = None
