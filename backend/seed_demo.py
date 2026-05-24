"""Seed demo signals into the DB so the UI looks alive."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import Base, engine, SessionLocal
from app.models import Signal
from datetime import datetime

Base.metadata.create_all(bind=engine)
db = SessionLocal()

DEMO = [
    dict(symbol="BTCUSDT",  direction="LONG",  signal_type="SHORT_SQUEEZE", confidence=88, risk_level="MEDIUM", ai_score=82.5, price=68420.5,  entry_low=68250, entry_high=68600, stop_loss=66900, take_profit_1=70100, take_profit_2=71500, take_profit_3=73800, risk_reward=2.2, change_1m=0.18, change_5m=0.62, change_15m=1.4, change_1h=3.8, change_24h=6.2, volume_24h=2_400_000_000, volume_spike_ratio=3.2, open_interest=8_200_000_000, funding_rate=0.0018, explanation="📊 Volume spike 3.2x | 🚀 Pump +3.8% in 1h | ⚡ Short Squeeze: rising OI + positive funding"),
    dict(symbol="ETHUSDT",  direction="LONG",  signal_type="PUMP",          confidence=74, risk_level="MEDIUM", ai_score=71.0, price=3_512.8,  entry_low=3500,  entry_high=3525,  stop_loss=3420,  take_profit_1=3640, take_profit_2=3750, take_profit_3=3900, risk_reward=2.5, change_1m=0.22, change_5m=0.85, change_15m=1.9, change_1h=4.1, change_24h=9.4, volume_24h=1_800_000_000, volume_spike_ratio=2.8, open_interest=5_100_000_000, funding_rate=0.0012, explanation="🚀 Pump detected +4.1% in 1h | 📊 Volume 2.8x average | 📈 OI +7.2%"),
    dict(symbol="SOLUSDT",  direction="SHORT", signal_type="DUMP",          confidence=69, risk_level="HIGH",   ai_score=58.3, price=182.45,  entry_low=182,   entry_high=183.5, stop_loss=188.0, take_profit_1=177,  take_profit_2=173,  take_profit_3=167,  risk_reward=1.8, change_1m=-0.3, change_5m=-1.1, change_15m=-2.5, change_1h=-4.2, change_24h=-7.8, volume_24h=980_000_000,   volume_spike_ratio=2.1, open_interest=1_200_000_000, funding_rate=-0.0022, explanation="🔻 Dump -4.2% in 1h | 💰 Extreme negative funding -0.22%"),
    dict(symbol="XRPUSDT",  direction="WATCH", signal_type="VOLUME_SPIKE",  confidence=55, risk_level="LOW",    ai_score=44.1, price=0.6124,  entry_low=0.610, entry_high=0.615, stop_loss=0.594, take_profit_1=0.628, take_profit_2=0.641, take_profit_3=0.658, risk_reward=2.0, change_1m=0.05, change_5m=0.28, change_15m=0.7,  change_1h=1.2, change_24h=3.1, volume_24h=620_000_000,   volume_spike_ratio=3.8, open_interest=540_000_000,   funding_rate=0.0001, explanation="📊 Volume spike 3.8x above average — monitoring for breakout"),
    dict(symbol="DOGEUSDT", direction="LONG",  signal_type="FUNDING",       confidence=62, risk_level="HIGH",   ai_score=53.7, price=0.18341, entry_low=0.182, entry_high=0.185, stop_loss=0.175, take_profit_1=0.192, take_profit_2=0.198, take_profit_3=0.207, risk_reward=2.3, change_1m=0.11, change_5m=0.44, change_15m=1.1,  change_1h=2.3, change_24h=11.2, volume_24h=850_000_000,  volume_spike_ratio=2.5, open_interest=720_000_000,   funding_rate=-0.0031, explanation="💰 Extreme negative funding -0.31% → shorts paying, LONG bias | 📊 Vol 2.5x"),
    dict(symbol="AVAXUSDT", direction="SHORT", signal_type="LONG_SQUEEZE",  confidence=71, risk_level="MEDIUM", ai_score=65.2, price=38.72,   entry_low=38.5,  entry_high=39.0,  stop_loss=40.8,  take_profit_1=36.8,  take_profit_2=35.2,  take_profit_3=33.5,  risk_reward=2.1, change_1m=-0.25, change_5m=-0.9, change_15m=-2.1, change_1h=-3.6, change_24h=-5.9, volume_24h=310_000_000,  volume_spike_ratio=2.9, open_interest=480_000_000,   funding_rate=-0.0019, explanation="⚡ Long Squeeze: negative funding + falling price + OI increase"),
    dict(symbol="BNBUSDT",  direction="LONG",  signal_type="OI_CHANGE",     confidence=58, risk_level="LOW",    ai_score=49.8, price=605.4,   entry_low=603,   entry_high=608,   stop_loss=590,   take_profit_1=622,   take_profit_2=638,   take_profit_3=660,   risk_reward=2.4, change_1m=0.08, change_5m=0.31, change_15m=0.9,  change_1h=1.8, change_24h=4.3, volume_24h=420_000_000,  volume_spike_ratio=1.9, open_interest=1_800_000_000, funding_rate=0.0008, explanation="📈 Open Interest +8.4% — new longs entering | 📊 Vol 1.9x"),
    dict(symbol="LINKUSDT", direction="WATCH", signal_type="VOLUME_SPIKE",  confidence=51, risk_level="LOW",    ai_score=38.6, price=14.82,   entry_low=14.7,  entry_high=14.9,  stop_loss=14.2,  take_profit_1=15.4,  take_profit_2=15.9,  take_profit_3=16.5,  risk_reward=1.9, change_1m=0.03, change_5m=0.19, change_15m=0.5,  change_1h=0.9, change_24h=2.7, volume_24h=180_000_000,  volume_spike_ratio=2.6, open_interest=220_000_000,   funding_rate=0.0002, explanation="📊 Volume spike 2.6x — watching for direction confirmation"),
]

for d in DEMO:
    db.add(Signal(is_active=True, alert_sent=False, **d))

db.commit()
print(f"✅ Seeded {len(DEMO)} demo signals into DB")
db.close()
