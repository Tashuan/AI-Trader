"""Pydantic models for StockBoy supervisor requests and responses."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ============================================================
# Request models
# ============================================================

class StockBoyActionRequest(BaseModel):
    """A paper-only adjustment command issued by StockBoy or an operator."""
    idempotency_key: str = Field(..., description="Unique key to prevent duplicate execution")
    runner_key: str = Field(..., description="Target runner (blitztrader, cryptorunner, scalprunner)")
    action_type: str = Field(..., description="close_position, partial_close, set_stop, set_target, set_trailing, cancel_order")
    target_position_id: Optional[int] = None
    target_order_id: Optional[int] = None
    quantity: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_sl_pct: Optional[float] = None
    trailing_activation_pct: Optional[float] = None
    rationale: str = ""
    policy_rule: str = ""


class StockBoyOverrideRequest(BaseModel):
    """A temporary bounded runner configuration override."""
    runner_key: str = Field(..., description="Target runner")
    field_path: str = Field(..., description="Dotted path within strategy params (e.g. exit_rules.stop_loss_pct)")
    new_value: Any = Field(..., description="New value for the field")
    rationale: str = ""
    expires_in_minutes: int = Field(default=60, ge=1, le=1440)


class StockBoyOverrideResetRequest(BaseModel):
    """Reset one or all runner overrides back to baseline defaults."""
    runner_key: Optional[str] = Field(None, description="Reset only this runner; omit for all runners")
    reason: str = "manual_reset"


class StockBoyKillSwitchRequest(BaseModel):
    """Engage or disengage the StockBoy emergency kill switch."""
    engaged: bool = True
    reason: str = ""


class StockBoyEnableRequest(BaseModel):
    """Enable or disable StockBoy supervisor actions."""
    enabled: bool = True


# ============================================================
# Response models
# ============================================================

class StockBoyRunnerHealth(BaseModel):
    runner_key: str
    agent_name: str
    agent_id: Optional[int] = None
    running: bool = False
    bot_type: str = "runner"
    last_error: Optional[str] = None
    cash: float = 0.0
    portfolio_value: float = 0.0
    open_positions: int = 0
    unrealized_pnl: float = 0.0
    today_pnl: float = 0.0
    active_overrides: int = 0
    heartbeat_age_seconds: Optional[float] = None
    last_cycle_at: Optional[str] = None


class StockBoyPositionDetail(BaseModel):
    position_id: int
    agent_id: int
    agent_name: str
    runner_key: str
    symbol: str
    market: str
    side: str
    quantity: float
    entry_price: float
    current_price: Optional[float] = None
    current_price_age_seconds: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_sl_pct: Optional[float] = None
    trailing_activation_pct: Optional[float] = None
    opened_at: Optional[str] = None
    age_seconds: Optional[float] = None
    missing_protection: bool = False
    stale_price: bool = False
    latest_assessment: Optional[str] = None


class StockBoyPendingOrderDetail(BaseModel):
    order_id: int
    agent_id: int
    agent_name: str
    runner_key: str
    symbol: str
    market: str
    side: str
    stop_price: float
    limit_price: Optional[float] = None
    quantity: float
    status: str
    created_at: str
    expires_at: str
    age_seconds: Optional[float] = None
    stale: bool = False


class StockBoyOverrideDetail(BaseModel):
    override_id: int
    runner_key: str
    field_path: str
    old_value: Any = None
    new_value: Any
    baseline_version: Optional[str] = None
    rationale: str = ""
    author: str = "stockboy"
    status: str = "active"
    expires_at: Optional[str] = None
    rolled_back_at: Optional[str] = None
    created_at: str


class StockBoyActionDetail(BaseModel):
    action_id: int
    idempotency_key: str
    cycle_id: Optional[int] = None
    runner_key: str
    action_type: str
    target_position_id: Optional[int] = None
    target_order_id: Optional[int] = None
    parameters: Dict[str, Any] = {}
    rationale: str = ""
    policy_rule: str = ""
    status: str = "pending"
    result: Dict[str, Any] = {}
    error: Optional[str] = None
    requested_at: str
    executed_at: Optional[str] = None
    created_at: str


class StockBoyObservationDetail(BaseModel):
    observation_id: int
    cycle_id: Optional[int] = None
    runner_key: Optional[str] = None
    severity: str = "info"
    category: str = ""
    message: str
    metadata: Dict[str, Any] = {}
    created_at: str


class StockBoyJournalEntry(BaseModel):
    entry_id: int
    runner_key: Optional[str] = None
    entry_type: str
    title: Optional[str] = None
    content: str
    metadata: Dict[str, Any] = {}
    created_at: str


class StockBoyCommentaryEntry(BaseModel):
    commentary_id: int
    kind: str = "status"
    severity: str = "info"
    content: str
    created_at: str


class StockBoyPortfolioOverview(BaseModel):
    total_equity: float = 0.0
    total_cash: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_today_pnl: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    open_position_count: int = 0
    pending_order_count: int = 0
    controlled_runner_count: int = 0
    active_override_count: int = 0
    data_fresh: bool = True


class StockBoyRiskAnomaly(BaseModel):
    category: str
    severity: str
    message: str
    runner_key: Optional[str] = None
    symbol: Optional[str] = None
    metadata: Dict[str, Any] = {}


class StockBoySnapshot(BaseModel):
    timestamp: str
    supervisor: "StockBoySupervisorStatus"
    portfolio: StockBoyPortfolioOverview
    runners: List[StockBoyRunnerHealth] = []
    positions: List[StockBoyPositionDetail] = []
    pending_orders: List[StockBoyPendingOrderDetail] = []
    overrides: List[StockBoyOverrideDetail] = []
    recent_actions: List[StockBoyActionDetail] = []
    recent_observations: List[StockBoyObservationDetail] = []
    recent_commentary: List[StockBoyCommentaryEntry] = []
    risk_anomalies: List[StockBoyRiskAnomaly] = []
    broader_agent_summary: List[Dict[str, Any]] = []


class StockBoySupervisorStatus(BaseModel):
    enabled: bool = False
    actions_enabled: bool = True
    mode: str = "paper"
    kill_switch: bool = False
    running: bool = False
    agent_id: Optional[int] = None
    last_cycle_at: Optional[str] = None
    next_cycle_at: Optional[str] = None
    last_heartbeat_at: Optional[str] = None
    last_error: Optional[str] = None
    cycles_run: int = 0
    controlled_runners: List[str] = []


class StockBoyActionResponse(BaseModel):
    success: bool
    action_id: Optional[int] = None
    status: str = "rejected"
    message: str = ""
    result: Dict[str, Any] = {}


class StockBoyOverrideResponse(BaseModel):
    success: bool
    override_id: Optional[int] = None
    message: str = ""
    rolled_back: List[Dict[str, Any]] = []


StockBoySnapshot.model_rebuild()
