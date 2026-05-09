"""
技术形态选股相关模型
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ConfigDict

from app.models.user import PyObjectId
from app.utils.timezone import now_tz


class PatternType(str, Enum):
    LAOYATOU = "laoyatou"
    N_SHAPE = "n_shape"


class PatternTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PatternUniverse(BaseModel):
    board: List[str] = Field(default_factory=lambda: ["MAIN", "STAR", "CHINEXT"])
    min_market_cap: Optional[float] = None
    industries: List[str] = Field(default_factory=list)


class PatternWindow(BaseModel):
    end_date: str = Field("auto", description="YYYY-MM-DD or 'auto'")
    lookback_days: int = Field(90, ge=30, le=365)


class PatternRules(BaseModel):
    # 通用参数（首期先用一套通用参数集）
    min_up_pct: float = Field(0.15, ge=0.0, le=5.0)
    max_drawdown: float = Field(0.50, ge=0.0, le=1.0)
    consolidation_volume_ratio: float = Field(0.75, ge=0.0, le=5.0)
    breakout_volume_ratio: float = Field(1.30, ge=0.0, le=20.0)


class PatternLLMOptions(BaseModel):
    enabled: bool = True
    max_reviews: int = Field(50, ge=0, le=500)


class PatternScreeningCreateRequest(BaseModel):
    pattern_types: List[PatternType] = Field(..., min_length=1)
    market: Literal["CN"] = "CN"
    universe: PatternUniverse = Field(default_factory=PatternUniverse)
    window: PatternWindow = Field(default_factory=PatternWindow)
    rules: PatternRules = Field(default_factory=PatternRules)
    llm: PatternLLMOptions = Field(default_factory=PatternLLMOptions)


class PatternScreeningCreateResponse(BaseModel):
    task_id: str
    status: PatternTaskStatus


class PatternProgressSnapshot(BaseModel):
    percent: int = Field(0, ge=0, le=100)
    step: str = "init"
    message: str = "任务已创建"


class PatternTaskStats(BaseModel):
    total_scanned: int = 0
    candidate_count: int = 0
    selected_count: int = 0


class PatternScreeningTask(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[PyObjectId] = Field(default_factory=PyObjectId, alias="_id")
    task_id: str
    user_id: PyObjectId

    status: PatternTaskStatus = PatternTaskStatus.QUEUED
    request: PatternScreeningCreateRequest

    created_at: datetime = Field(default_factory=now_tz)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    progress: PatternProgressSnapshot = Field(default_factory=PatternProgressSnapshot)
    stats: PatternTaskStats = Field(default_factory=PatternTaskStats)

    summary: Optional[str] = None
    error: Optional[str] = None

    cancel_requested: bool = False


class PatternScreeningEvent(BaseModel):
    task_id: str
    timestamp: datetime = Field(default_factory=now_tz)
    step: str
    title: str
    message: str
    progress: int = Field(0, ge=0, le=100)
    data: Dict[str, Any] = Field(default_factory=dict)


class PatternResultListItem(BaseModel):
    code: str
    name: str
    price: float
    change_amount: float
    pct_chg: float
    market_cap: float

    pattern_type: PatternType
    pattern_name: str
    pattern_score: int = Field(0, ge=0, le=100)
    recommendation_score: int = Field(0, ge=0, le=100)
    signal_date: str
    brief_reason: str


class PatternResultListResponse(BaseModel):
    total: int
    items: List[PatternResultListItem]


class PatternResultDetail(BaseModel):
    code: str
    name: str

    pattern_type: PatternType
    pattern_score: int = Field(0, ge=0, le=100)
    recommendation_score: int = Field(0, ge=0, le=100)

    pattern_breakdown: Dict[str, str] = Field(default_factory=dict)
    analysis: str
    trend_expectation: str

    buy_price_range: Tuple[float, float]
    position_suggestion: str
    stop_loss: float
    risk_points: List[str]
    invalid_conditions: List[str]

    evidence: Dict[str, Any] = Field(default_factory=dict)


class PatternTaskResponse(BaseModel):
    task_id: str
    status: PatternTaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: PatternProgressSnapshot
    stats: PatternTaskStats
    summary: Optional[str] = None
    error: Optional[str] = None

