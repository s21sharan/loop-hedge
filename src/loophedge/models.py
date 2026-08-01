import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


M20_8 = Numeric(20, 8)

# Wide enough for Kalshi event tickers (e.g. KXHIGHNY-26JUL28-B82.5) and
# Polymarket CLOB token IDs, which are uint256 values rendered in decimal.
SYMBOL_LEN = String(96)


class Bar(Base):
    __tablename__ = "bars"
    symbol: Mapped[str] = mapped_column(SYMBOL_LEN, primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(M20_8)
    high: Mapped[Decimal] = mapped_column(M20_8)
    low: Mapped[Decimal] = mapped_column(M20_8)
    close: Mapped[Decimal] = mapped_column(M20_8)
    volume: Mapped[Decimal] = mapped_column(M20_8)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    ts_created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC))
    strategy_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(SYMBOL_LEN)
    side: Mapped[str] = mapped_column(String(8))     # long | short | flat
    size_pct: Mapped[Decimal] = mapped_column(M20_8)
    status: Mapped[str] = mapped_column(String(16))  # candidate|approved|rejected|executed|killed
    maker_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    checker_verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    signal_id: Mapped[str | None] = mapped_column(
        ForeignKey("signals.id"), nullable=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(SYMBOL_LEN)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[Decimal] = mapped_column(M20_8)
    price: Mapped[Decimal] = mapped_column(M20_8)
    fees: Mapped[Decimal] = mapped_column(M20_8)
    venue: Mapped[str] = mapped_column(String(32))   # simulator | binance_testnet


class Position(Base):
    __tablename__ = "positions"
    symbol: Mapped[str] = mapped_column(SYMBOL_LEN, primary_key=True)
    qty: Mapped[Decimal] = mapped_column(M20_8)
    avg_entry: Mapped[Decimal] = mapped_column(M20_8)
    unrealized_pnl: Mapped[Decimal] = mapped_column(M20_8)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    cash: Mapped[Decimal] = mapped_column(M20_8)
    equity: Mapped[Decimal] = mapped_column(M20_8)
    drawdown_pct: Mapped[Decimal] = mapped_column(M20_8)


class Strategy(Base):
    __tablename__ = "strategies"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))   # active | pending | retired
    source_path: Mapped[str] = mapped_column(String(256))
    hyperparams: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    retired_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Backtest(Base):
    __tablename__ = "backtests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    strategy_id: Mapped[str] = mapped_column(String(64))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sharpe: Mapped[Decimal] = mapped_column(M20_8)
    max_dd_pct: Mapped[Decimal] = mapped_column(M20_8)
    t_stat: Mapped[Decimal] = mapped_column(M20_8)
    trade_count: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True,
                                    default=lambda: str(uuid.uuid4()))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    actions_taken: Mapped[dict] = mapped_column(JSON, default=dict)


class Contract(Base):
    __tablename__ = "contracts"
    symbol: Mapped[str] = mapped_column(SYMBOL_LEN, primary_key=True)
    venue: Mapped[str] = mapped_column(String(32))
    open_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_value: Mapped[Decimal | None] = mapped_column(M20_8, nullable=True)
    resolution_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contract_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class WeatherForecast(Base):
    __tablename__ = "weather_forecasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(8))
    forecast_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temp_mean_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    temp_std_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    source: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint(
            "city", "forecast_ts", "valid_ts", "source",
            name="uq_weather_forecast_key"
        ),
    )
