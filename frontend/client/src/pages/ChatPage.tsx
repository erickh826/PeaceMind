import { useState, useRef, useEffect, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import BoonLogo from "@/components/BoonLogo";
import MessageBubble from "@/components/MessageBubble";
import TypingIndicator from "@/components/TypingIndicator";
import CrisisCard from "@/components/CrisisCard";
import CharCounter from "@/components/CharCounter";
import PerplexityAttribution from "@/components/PerplexityAttribution";
import { Send, Moon, Sun } from "lucide-react";

// ── Constants ─────────────────────────────────────────────────
const MAX_CHARS = 1000;
const WARN_THRESHOLD = 800;   // 橘色提示開始
const SOFT_CAP = 950;          // 友善文案切換

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "crisis" | "intercepted";
  content: string;
  timestamp: Date;
}

interface ApiResponse {
  reply: string;
  intercepted: boolean;
  crisis: boolean;
}

// ── Welcome message ───────────────────────────────────────────
const WELCOME: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "你好，我是 Boon。\n\n不管你現在心情如何，我都在這裡陪你。有什麼想說的，或者想聊聊今天的心情嗎？",
  timestamp: new Date(),
};

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME]);
  const [input, setInput] = useState("");
  const [darkMode, setDarkMode] = useState(
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Dark mode toggle
  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
  }, [darkMode]);

  // Auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  }, [input]);

  const charCount = input.length;
  const isOverLimit = charCount > MAX_CHARS;
  const isWarning = charCount >= WARN_THRESHOLD && !isOverLimit;

  // Build conversation history (last 10 rounds = 20 messages, skip welcome)
  const getHistory = useCallback(() => {
    return messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .slice(-20)
      .map((m) => ({
        role: m.role === "user" ? "user" : "assistant",
        content: m.content,
      }));
  }, [messages]);

  const mutation = useMutation({
    mutationFn: async (text: string) => {
      const res = await apiRequest("POST", "/api/v1/chat", {
        message: text,
        history: getHistory(),
      });
      return res.json() as Promise<ApiResponse>;
    },
    onSuccess: (data) => {
      const role: ChatMessage["role"] = data.crisis
        ? "crisis"
        : data.intercepted
        ? "intercepted"
        : "assistant";

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role,
          content: data.reply,
          timestamp: new Date(),
        },
      ]);
    },
    onError: () => {
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "intercepted",
          content:
            "抱歉，我現在好像出了一點問題，請稍後再試。如果你現在很難過，可以先聯繫生命熱線：2382 0000。",
          timestamp: new Date(),
        },
      ]);
    },
  });

  const handleSend = () => {
    const text = input.trim();
    if (!text || isOverLimit || mutation.isPending) return;

    setMessages((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: "user",
        content: text,
        timestamp: new Date(),
      },
    ]);
    setInput("");
    mutation.mutate(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── Empathetic hint copy ──────────────────────────────────
  const getHintCopy = () => {
    if (charCount >= SOFT_CAP)
      return "現在送出剛剛好，阿本已經準備好傾聽了 ✦";
    if (charCount >= WARN_THRESHOLD)
      return "分段說，阿本能聽得更清楚喔！";
    return null;
  };
  const hintCopy = getHintCopy();

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* ── Header ── */}
      <header className="flex items-center justify-between px-4 py-3 border-b border-border bg-card/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <BoonLogo size={36} />
          <div>
            <h1 className="font-semibold text-base text-foreground leading-tight">
              Boon
            </h1>
            <p className="text-xs text-muted-foreground">心理健康支持助理</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="hidden sm:block text-xs text-muted-foreground px-2 py-1 rounded-full bg-muted border border-border">
            非醫療用途 · PoC
          </span>
          <button
            onClick={() => setDarkMode((d) => !d)}
            className="p-2 rounded-full hover:bg-muted transition-colors"
            aria-label={darkMode ? "切換淺色模式" : "切換深色模式"}
            data-testid="button-theme-toggle"
          >
            {darkMode ? (
              <Sun size={18} className="text-muted-foreground" />
            ) : (
              <Moon size={18} className="text-muted-foreground" />
            )}
          </button>
        </div>
      </header>

      {/* ── Message list ── */}
      <main
        className="flex-1 overflow-y-auto px-4 py-6 space-y-4"
        aria-label="對話記錄"
        data-testid="chat-messages"
      >
        <div className="max-w-2xl mx-auto space-y-4">
          {messages.map((msg) =>
            msg.role === "crisis" ? (
              <CrisisCard key={msg.id} content={msg.content} />
            ) : (
              <MessageBubble key={msg.id} message={msg} />
            )
          )}
          {mutation.isPending && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>
      </main>

      {/* ── Empathetic hint bar ── */}
      {hintCopy && (
        <div
          className={`px-4 py-2 text-center text-sm transition-all duration-300 ${
            charCount >= SOFT_CAP
              ? "bg-orange-50 dark:bg-orange-950/30 char-danger-text"
              : "bg-amber-50 dark:bg-amber-950/30 char-warning-text"
          }`}
          aria-live="polite"
          data-testid="hint-empathetic"
        >
          {hintCopy}
        </div>
      )}

      {/* ── Input area ── */}
      <footer className="px-4 pb-4 pt-2 bg-card/80 backdrop-blur-sm border-t border-border">
        <div className="max-w-2xl mx-auto">
          <div
            className={`flex items-end gap-2 rounded-2xl border transition-all duration-200 bg-background p-3 ${
              isOverLimit
                ? "border-orange-400 ring-1 ring-orange-300"
                : isWarning
                ? "border-amber-300 ring-1 ring-amber-200"
                : "border-border focus-within:ring-1 focus-within:ring-ring focus-within:border-ring"
            }`}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="跟我說說你現在的感受…"
              rows={1}
              maxLength={MAX_CHARS + 50}
              disabled={mutation.isPending}
              className="flex-1 resize-none bg-transparent outline-none text-foreground placeholder:text-muted-foreground text-base leading-relaxed disabled:opacity-50"
              style={{ minHeight: "24px", maxHeight: "180px" }}
              data-testid="input-message"
              aria-label="輸入訊息"
            />
            <div className="flex items-center gap-2 shrink-0">
              <CharCounter count={charCount} max={MAX_CHARS} warn={WARN_THRESHOLD} />
              <button
                onClick={handleSend}
                disabled={!input.trim() || isOverLimit || mutation.isPending}
                className="p-2 rounded-xl bg-primary text-primary-foreground hover:opacity-90 active:scale-95 transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed"
                aria-label="送出訊息"
                data-testid="button-send"
              >
                <Send size={18} />
              </button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground mt-2 text-center">
            Boon 不是醫療工具，不提供診斷或處方。如有緊急情況請致電 999。
          </p>
        </div>
      </footer>

      <PerplexityAttribution />
    </div>
  );
}
