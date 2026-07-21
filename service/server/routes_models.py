from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, field_validator


class AgentLogin(BaseModel):
    name: str
    password: str
    client_type: Optional[str] = None


class AgentRegister(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    password: str
    wallet_address: Optional[str] = None
    initial_balance: float = 100000.0
    positions: Optional[List[dict]] = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        if value is None:
            return None
        normalized = str(value).strip().lower()
        return normalized or None


class AgentTokenRecoveryRequest(BaseModel):
    agent_id: Optional[int] = None
    name: Optional[str] = None


class AgentTokenRecoveryConfirm(BaseModel):
    agent_id: Optional[int] = None
    name: Optional[str] = None
    challenge: str
    signature: str


class AgentPasswordResetRequest(BaseModel):
    agent_id: Optional[int] = None
    name: Optional[str] = None


class AgentPasswordResetConfirm(BaseModel):
    agent_id: Optional[int] = None
    name: Optional[str] = None
    challenge: str
    signature: str
    new_password: str


class RealtimeSignalRequest(BaseModel):
    market: str
    action: str
    symbol: str
    price: float
    quantity: float
    content: Optional[str] = None
    executed_at: str
    token_id: Optional[str] = None
    outcome: Optional[str] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    order_type: Optional[str] = "market"
    limit_price: Optional[float] = None
    time_in_force: Optional[str] = "gtc"
    expires_after_minutes: Optional[int] = None


class StrategyRequest(BaseModel):
    market: str
    title: str
    content: str
    symbols: Optional[str] = None
    tags: Optional[str] = None
    challenge_key: Optional[str] = None
    mission_key: Optional[str] = None
    team_key: Optional[str] = None


class DiscussionRequest(BaseModel):
    market: str
    symbol: Optional[str] = None
    title: str
    content: str
    tags: Optional[str] = None
    challenge_key: Optional[str] = None
    mission_key: Optional[str] = None
    team_key: Optional[str] = None


class ChallengeCreateRequest(BaseModel):
    challenge_key: Optional[str] = None
    title: str
    description: Optional[str] = None
    market: str
    symbol: Optional[str] = None
    challenge_type: str = "multi-agent"
    mode: str = "individual"
    status: Optional[str] = None
    scoring_method: str = "return-only"
    initial_capital: float = 100000.0
    max_position_pct: float = 100.0
    max_drawdown_pct: float = 100.0
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    rules_json: Optional[Dict[str, Any]] = None
    experiment_key: Optional[str] = None


class ChallengeJoinRequest(BaseModel):
    variant_key: Optional[str] = None
    starting_cash: Optional[float] = None


class ChallengeSubmissionRequest(BaseModel):
    submission_type: str = "manual"
    content: Optional[str] = None
    prediction_json: Optional[Dict[str, Any]] = None
    signal_id: Optional[int] = None


class ChallengeTradeRequest(BaseModel):
    side: str
    symbol: Optional[str] = None
    token_id: Optional[str] = None
    outcome: Optional[str] = None
    price: float
    quantity: float
    content: Optional[str] = None
    executed_at: Optional[str] = None


class ChallengeTeamCreateRequest(BaseModel):
    team_key: Optional[str] = None
    name: str
    role: Optional[str] = "captain"
    variant_key: Optional[str] = None
    starting_cash: Optional[float] = None


class ChallengeTeamJoinRequest(BaseModel):
    role: Optional[str] = "member"
    variant_key: Optional[str] = None


class ChallengeTeamSubmissionRequest(BaseModel):
    submission_type: str = "team_thesis"
    content: Optional[str] = None
    prediction_json: Optional[Dict[str, Any]] = None


class ChallengeSubmissionVoteRequest(BaseModel):
    vote: str
    content: Optional[str] = None


class ChallengeSettleRequest(BaseModel):
    force: bool = False


class ExperimentCreateRequest(BaseModel):
    experiment_key: Optional[str] = None
    title: str
    description: Optional[str] = None
    status: str = "active"
    unit_type: str = "agent"
    variants_json: Optional[List[Dict[str, Any]]] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None


class ExperimentStatusRequest(BaseModel):
    status: str


class ExperimentNotificationRequest(BaseModel):
    message_type: str
    title: str
    content: str
    variant_key: Optional[str] = None
    agent_ids: Optional[List[int]] = None
    dry_run: bool = True
    limit: int = 500
    data: Optional[Dict[str, Any]] = None
    create_task: bool = False
    task_type: Optional[str] = None
    input_data: Optional[Dict[str, Any]] = None
    challenge_key: Optional[str] = None
    mission_key: Optional[str] = None
    team_key: Optional[str] = None
    target: Optional[str] = None


class ExperimentTaskRequest(BaseModel):
    task_type: str
    input_data: Optional[Dict[str, Any]] = None
    experiment_key: Optional[str] = None
    variant_key: Optional[str] = None
    challenge_key: Optional[str] = None
    mission_key: Optional[str] = None
    team_key: Optional[str] = None
    agent_ids: Optional[List[int]] = None
    target: Optional[str] = None
    dry_run: bool = True
    limit: int = 500


class RewardReverseRequest(BaseModel):
    reason: str = "reversed"


class ResearchExportRequest(BaseModel):
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    experiment_key: Optional[str] = None
    variant_key: Optional[str] = None
    market: Optional[str] = None
    limit: int = 100000
    offset: int = 0


class TeamMissionCreateRequest(BaseModel):
    mission_key: Optional[str] = None
    title: str
    description: Optional[str] = None
    market: str
    symbol: Optional[str] = None
    mission_type: str = "consensus"
    status: Optional[str] = None
    team_size_min: int = 2
    team_size_max: int = 5
    assignment_mode: str = "random"
    required_roles_json: Optional[List[str]] = None
    start_at: Optional[str] = None
    submission_due_at: Optional[str] = None
    rules_json: Optional[Dict[str, Any]] = None
    experiment_key: Optional[str] = None


class TeamJoinRequest(BaseModel):
    team_key: Optional[str] = None
    name: Optional[str] = None
    role: Optional[str] = None
    variant_key: Optional[str] = None


class TeamSubmissionRequest(BaseModel):
    title: str
    content: str
    prediction_json: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None


class TeamMessageLinkRequest(BaseModel):
    signal_id: int
    message_type: str = "signal"
    content: Optional[str] = None
    metadata_json: Optional[Dict[str, Any]] = None


class TeamMissionSettleRequest(BaseModel):
    force: bool = False
    assignment_mode: Optional[str] = None


class ReplyRequest(BaseModel):
    signal_id: int
    content: str


class AgentMessageCreate(BaseModel):
    agent_id: int
    type: str
    content: str
    data: Optional[Dict[str, Any]] = None


class AgentMessagesMarkReadRequest(BaseModel):
    categories: List[str]


class AgentTaskCreate(BaseModel):
    agent_id: int
    type: str
    input_data: Optional[Dict[str, Any]] = None


class FollowRequest(BaseModel):
    leader_id: int


class UserSendCodeRequest(BaseModel):
    email: EmailStr


class UserRegisterRequest(BaseModel):
    email: EmailStr
    code: str
    password: str


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class PointsTransferRequest(BaseModel):
    to_user_id: int
    amount: int


class PointsExchangeRequest(BaseModel):
    amount: int


class AgentConfigCreate(BaseModel):
    name: str
    email: Optional[str] = None
    password: Optional[str] = None
    tagline: str = ""
    bio: str = ""
    risk_tolerance: str = "moderate"
    position_sizing: str = "medium"
    hold_period: str = "swing"
    max_positions: int = 6
    confidence_threshold: float = 0.60
    fomo_resistance: float = 0.70
    loss_aversion: float = 0.70
    conviction_multiplier: float = 1.2
    voice: str = ""
    emoji_frequency: str = "rare"
    publishes_reasoning: bool = True
    trash_talk: bool = False
    strategy_type: str = ""
    watchlist: Optional[List[str]] = None
    quirks: Optional[List[str]] = None
    initial_cash: float = 100000.0
    auto_start: bool = False
    poll_interval: int = 300
    api_base: str = "http://localhost:8000/api"
    generate_files: bool = True


class AgentConfigUpdate(BaseModel):
    tagline: Optional[str] = None
    bio: Optional[str] = None
    risk_tolerance: Optional[str] = None
    position_sizing: Optional[str] = None
    hold_period: Optional[str] = None
    max_positions: Optional[int] = None
    confidence_threshold: Optional[float] = None
    fomo_resistance: Optional[float] = None
    loss_aversion: Optional[float] = None
    conviction_multiplier: Optional[float] = None
    voice: Optional[str] = None
    emoji_frequency: Optional[str] = None
    publishes_reasoning: Optional[bool] = None
    trash_talk: Optional[bool] = None
    strategy_type: Optional[str] = None
    watchlist: Optional[List[str]] = None
    quirks: Optional[List[str]] = None
    status: Optional[str] = None
    auto_start: Optional[bool] = None
    poll_interval: Optional[int] = None
    api_base: Optional[str] = None
    generate_files: bool = False
