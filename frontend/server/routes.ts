/**
 * Express Routes — PeaceMind Frontend Server
 *
 * This proxy forwards /api/v1/chat requests to the FastAPI backend.
 * In development: backend runs on localhost:8000
 * In production:  set FASTAPI_URL env variable
 */
import type { Express, Request, Response } from "express";
import { createServer, type Server } from "http";

const FASTAPI_URL = process.env.FASTAPI_URL || "http://localhost:8000";

export async function registerRoutes(
  httpServer: Server,
  app: Express
): Promise<Server> {

  // Proxy /api/v1/* → FastAPI backend
  app.post("/api/v1/chat", async (req: Request, res: Response) => {
    try {
      const response = await fetch(`${FASTAPI_URL}/api/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body),
      });

      const data = await response.json();

      if (!response.ok) {
        return res.status(response.status).json(data);
      }

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
