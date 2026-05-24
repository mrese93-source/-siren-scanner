from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text
from sqlalchemy.sql import func
from .database import Base


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True)
    direction = Column(String)          # LONG / SHORT / WATCH
    signal_type = Column(String)        # VOLUME_SPIKE / PUMP / DUMP / FUNDING / OI_CHANGE / SQUEEZE
    confidence = Column(Float)          # 0-100
    risk_level = Column(String)         # LOW / MEDIUM / HIGH
    ai_score = Column(Float)            # 0-100
    price = Column(Float)
    entry_low = Column(Float)
    entry_high = Column(Float)
    stop_loss = Column(Float)
    take_profit_1 = Column(Float)
    take_profit_2 = Column(Float)
    take_profit_3 = Column(Float)
    risk_reward = Column(Float)
    change_1m = Column(Float)
    change_5m = Column(Float)
    change_15m = Column(Float)
    change_1h = Column(Float)
    change_24h = Column(Float)
    volume_24h = Column(Float)
    volume_spike_ratio = Column(Float)
    open_interest = Column(Float)
    funding_rate = Column(Float)
    explanation = Column(Text)
    is_active = Column(Boolean, default=True)
    alert_sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String)
    signal_type = Column(String)
    message = Column(Text)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    success = Column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True)
    username = Column(String)
    is_premium = Column(Boolean, default=False)
    subscription_expires = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
