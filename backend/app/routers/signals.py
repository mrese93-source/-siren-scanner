from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from ..database import get_db
from ..models import Signal
from ..schemas import SignalOut, ScannerStatus
from ..services.scanner import get_scanner_status

router = APIRouter(prefix="/api", tags=["signals"])


@router.get("/signals", response_model=list[SignalOut])
def list_signals(
    active_only: bool = True,
    direction: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(Signal)
    if active_only:
        q = q.filter(Signal.is_active == True)
    if direction:
        q = q.filter(Signal.direction == direction.upper())
    return q.order_by(Signal.ai_score.desc(), Signal.created_at.desc()).limit(limit).all()


@router.get("/signals/{symbol}", response_model=list[SignalOut])
def signals_by_symbol(
    symbol: str,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    return (
        db.query(Signal)
        .filter(Signal.symbol == symbol.upper())
        .order_by(Signal.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/status", response_model=ScannerStatus)
def scanner_status():
    return get_scanner_status()


@router.get("/top", response_model=list[SignalOut])
def top_signals(
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db),
):
    return (
        db.query(Signal)
        .filter(Signal.is_active == True)
        .order_by(Signal.ai_score.desc())
        .limit(limit)
        .all()
    )
