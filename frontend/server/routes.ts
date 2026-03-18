/**
 * Express Routes — PeaceMind Frontend Server
 *
 * Local dev:  proxies /api/v1/* → FastAPI at localhost:8000
 * Production (Vercel): frontend is static, VITE_API_URL points directly
 *                      to backend Vercel deployment — this proxy is not used.
 */
import type { Express, Request, Response } from "express";
import { createServer, type Server } from "http";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function registerRoutes(
  httpServer: Server,
  app: Express
): Promise<Server> {

  // Local-dev proxy: /api/v1/* → FastAPI backend
  app.post("/api/v1/chat", async (req: Request, res: Response) => {
    try {
      const response = await fetch(`${FASTAPI_URL}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body),
      });
      const data = await response.json();
      if (!response.ok) return res.status(response.status).json(data);
      return res.json(data);
    } catch (err) {
      console.error("[proxy] FastAPI unreachable:", err);
      return res.status(503).json({
        reply: "服務暫時無法使用，請稍後再試。如有緊急情況請致電 999。",
        intercepted: true,
        crisis: false,
      });
    }
  });

  return httpServer;
}
