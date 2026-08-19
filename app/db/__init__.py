"""
資料庫模組（Phase 0）
提供 async SQLAlchemy engine + session factory，取代 InMemoryConversationStore。

使用方式：
    from app.db import get_session

    async with get_session() as session:
        ...

── Driver：psycopg（v3），非 asyncpg（Hotfix, 2026-08）──────────────────────
原本用 asyncpg，但在 Vercel 的 Python Serverless（底層是 AWS Lambda 類沙盒 +
uvloop）上，asyncpg 建立 SSL 連線時會噴 `OSError: [Errno 16] Device or
resource busy`——這是 uvloop 的 SSL transport 實作在建立連線時用了某種
file descriptor 複製技巧，在 Lambda 的沙盒限制下會失敗，屬於已知的
asyncpg + uvloop + Lambda 類環境相容性問題，不是連線字串設錯。

改用 psycopg（v3）的 async 支援後不會踩到這個問題，SQLAlchemy 2.0+ 原生
支援 `postgresql+psycopg://` 這個 URL scheme 走 psycopg3 的 asyncio 介面。

── PgBouncer / Transaction-mode pooler 相容性 ────────────────────────────────
Vercel Serverless 這類環境建議透過 PgBouncer 的 transaction-mode pooler
連線（例如 Supabase 的 "Transaction pooler"，port 6543），因為每次 function
呼叫都可能是全新連線，直連 Postgres 容易把連線數用滿。

但 server-side prepared statement 快取跟 PgBouncer transaction 模式不相容
（同一個 statement 名稱可能被不同的底層連線重用）。psycopg3 的對應設定是
`prepare_threshold=None`（關閉自動 server-side prepare），對一般直連
Postgres 也完全無害，這裡無條件加上。

另外，Supabase 給的 transaction pooler 連線字串常帶有 `?pgbouncer=true`
查詢參數（給 Prisma 等工具識別用）。這個參數不是合法的 psycopg 連線參數，
若原封不動放在 DATABASE_URL 裡會導致連線失敗，這裡在建立 engine 前先過濾掉。

── Serverless 連線池相容性 ────────────────────────────────────────────────────
`_engine` 是模組層級的全域單例。Vercel 的 Python function 有時會重用
「暖機」的 process，process 被凍結又解凍時，連線池裡持有的 socket 可能
處在壞掉的狀態。解法：改用 `NullPool`，每次 checkout 都建立全新連線、
用完即關閉，不跨 request 重用底層 socket——反正 PgBouncer/Transaction
Pooler 本身就已經在做連線池了，應用層不需要自己再維護一層。
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.base import Base

# Windows 預設 ProactorEventLoop 與 psycopg async 不相容（同 migrations/env.py
# 的修正）。這裡是所有 async DB 存取的共用入口，本機用 uvicorn 跑 API 一樣會
# 經過這個模組，所以修復要放在這裡，不能只放在 migrations/env.py。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _normalize_database_url(database_url: str) -> str:
    """
    移除 psycopg 不認得的查詢參數（目前已知：pgbouncer=true，Prisma 風格
    連線字串常見於 Supabase/Neon 的 transaction pooler URL）。
    """
    parts = urlsplit(database_url)
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() != "pgbouncer"]
    new_query = urlencode(query_pairs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL 未設定。請參考 .env.example 設定本地或雲端 Postgres 連線字串。"
            )
        _engine = create_async_engine(
            _normalize_database_url(database_url),
            # NullPool：不在 process 內重用連線，見檔案頂端「Serverless 連線池相容性」說明
            poolclass=NullPool,
            # PgBouncer transaction-mode pooler 相容性：關閉 psycopg 的自動 server-side prepare
            connect_args={"prepare_threshold": None},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Async context manager：`async with get_session() as session: ...`"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def dispose_engine() -> None:
    """供測試/關閉服務時呼叫，釋放連線池。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
