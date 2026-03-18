"""
Vercel Serverless 入口 — 重新導出 FastAPI app
Vercel 會尋找此檔案中的 `app` 實例
"""
from app.main import app  # noqa: F401  ← Vercel 需要此行
