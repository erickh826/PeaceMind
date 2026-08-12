"""SQLAlchemy Declarative Base — 所有 ORM model 的共同基底。"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
