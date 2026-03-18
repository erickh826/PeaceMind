"""
PeaceMind — 心理諮詢 AI 助理 PoC「Boon」
FastAPI 主入口
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import chat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PeaceMind API starting up...")
    yield
    logger.info("PeaceMind API shutting down...")


app = FastAPI(
    title="PeaceMind API",
    description="心理諮詢 AI 助理 Boon — PoC Backend with Defense-in-Depth",
    version="0.1.0",
    lifespan=lifespan,
    # 生產環境應關閉 docs
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — PoC 階段允許所有來源，生產環境應限制
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok", "service": "PeaceMind", "version": "0.1.0"}
