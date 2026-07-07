from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BarClosed(_Strict):
    symbol: str
    timeframe: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class SignalCandidate(_Strict):
    signal_id: str
    strategy_id: str
    symbol: str
    side: Literal["long", "short", "flat"]
    size_pct: Decimal
    reasoning: str


class SignalVerified(_Strict):
    signal_id: str
    verdict: Literal["approve"]
    notes: str | None = None


class SignalRejected(_Strict):
    signal_id: str
    verdict: Literal["reject", "needs_revision"]
    reason: str


class CircuitBroken(_Strict):
    ts: datetime
    drawdown_pct: Decimal
    action: Literal["flatten_all", "pause_makers"]
