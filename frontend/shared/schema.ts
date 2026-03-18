/**
 * PeaceMind — Shared Schema
 * No database required for this PoC; all conversation state is in-memory (React).
 * Schema file kept minimal to satisfy template requirements.
 */
import { z } from "zod";

export const chatMessageSchema = z.object({
  role: z.enum(["user", "assistant"]),
  content: z.string(),
});

export const chatRequestSchema = z.object({
  message: z.string().min(1).max(1600),
  history: z.array(chatMessageSchema).max(20).default([]),
});

export const chatResponseSchema = z.object({
  reply: z.string(),
  intercepted: z.boolean().default(false),
  crisis: z.boolean().default(false),
});

export type ChatRequest = z.infer<typeof chatRequestSchema>;
export type ChatResponse = z.infer<typeof chatResponseSchema>;
