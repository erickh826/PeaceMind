import { useState, useRef, useEffect, useCallback } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import BoonLogo from "@/components/BoonLogo";
import MessageBubble from "@/components/MessageBubble";
import TypingIndicator from "@/components/TypingIndicator";
import CrisisCard from "@/components/CrisisCard";
import CharCounter from "@/components/CharCounter";
import PerplexityAttribution from "@/components/PerplexityAttribution";
import { Link } from "wouter";
import { Send, Moon, Sun, RotateCcw, BookOpen, Play } from "lucide-react";

// ── Recommendation engine ─────────────────────────────────────────────────────
interface CourseRec {
  id: string;
  title: string;
  youtubeId: string;
  category: string;
  categoryStyle: string;
  note: string;
}

// keyword → course mapping (ordered: first match wins)
const REC_RULES: Array<{ keywords: RegExp; course: CourseRec }> = [
  {
    keywords: /靜觀|冥想|breathe|呼吸|平靜|放鬆|reset|五分鐘|3分鐘|三分鐘/i,
    course: {
      id: "mindful-3min",
      title: "【靜觀冥想】靜觀三分鐘",
      youtubeId: "846VoF-JPno",
      category: "靜觀冥想",
      categoryStyle: "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300",
      note: "三分鐘就夠，隨時隨地重設一下自己。",
    },
  },
  {
    keywords: /焦慮|擔心|緊張|不安|anxiety|worry|惶恐|慌/i,
    course: {
      id: "anxiety-relief",
      title: "當焦慮感來襲，心理師教你這樣做",
      youtubeId: "bY4Ux7K-LAs",
      category: "情緒管理",
      categoryStyle: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
      note: "心理師親身示範，幫你在焦慮湧現時找回掌控感。",
    },
  },
  {
    keywords: /情緒|憤怒|難過|傷心|崩潰|大腦|科學|神經|emotion|感受/i,
    course: {
      id: "brain-emotion",
      title: "如何管理情緒？從大腦科學的角度來看",
      youtubeId: "27zBdxVGBOU",
      category: "大腦科學",
      categoryStyle: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
      note: "了解情緒背後的大腦機制，才能真正管理它。",
    },
  },
  {
    keywords: /拖延|procrastin|唔想做|做唔到|懶|deadline|沒動力|冇動力/i,
    course: {
      id: "procrastination",
      title: "三步驟教你改善拖延症！",
      youtubeId: "SVP10jrnkb0",
      category: "自我提升",
      categoryStyle: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
      note: "拖延不是懶，是情緒調節問題。三步驟打破循環。",
    },
  },
  {
    keywords: /正向|肯定|自信|affirmation|自我價值|自尊|積極/i,
    course: {
      id: "affirmations",
      title: "每日 10 分鐘 廣東話肯定句",
      youtubeId: "-2KNgktM6Vo",
      category: "正向思維",
      categoryStyle: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
      note: "用廣東話溫柔地與自己對話，每天十分鐘。",
    },
  },
  {
    keywords: /關係|愛情|伴侶|另一半|分手|界線|boundary|沉船|toxic/i,
    course: {
      id: "love-boundary",
      title: "沉船其實不是愛",
      youtubeId: "ys7gELVX174",
      category: "人際關係",
      categoryStyle: "bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300",
      note: "辨識關係中的邊界，學懂愛自己才是真正的愛。",
    },
  },
  {
    keywords: /內在|小孩|童年|原生家庭|inner child|療癒|heal|創傷|trauma/i,
    course: {
      id: "inner-child",
      title: "療癒你的內在小孩",
      youtubeId: "rJ-2DXLVDEY",
      category: "內在療癒",
      categoryStyle: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
      note: "與自己的內在小孩和解，重新連結內心深處的自己。",
    },
  },
  // Fallback: stress / burnout / general 壓力 → mindful
  {
    keywords: /壓力|burnout|burnout|透唔過氣|喘不過氣|累|身心疲憊|攰|煩/i,
    course: {
      id: "mindful-3min",
      title: "【靜觀冥想】靜觀三分鐘",
      youtubeId: "846VoF-JPno",
      category: "靜觀冥想",
      categoryStyle: "bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300",
      note: "先喘口氣，三分鐘靜觀幫你重拾一點空間。",
    },
  },
];

function matchRecommendation(text: string): CourseRec | null {
  for (const rule of REC_RULES) {
    if (rule.keywords.test(text)) return rule.course;
  }
  return null;
}

// ── Inline Recommendation Card (shown below Boon's bubble) ───────────────────
function RecCard({ rec }: { rec: CourseRec }) {
  const thumb = `https://img.youtube.com/vi/${rec.youtubeId}/mqdefault.jpg`;
  return (
    <Link href={`/courses#${rec.id}`}>
      <div
        className="ml-[42px] mt-2 flex gap-3 p-3 rounded-2xl border border-border bg-card/80 hover:bg-card hover:shadow-md transition-all duration-200 cursor-pointer group max-w-[78%] sm:max-w-[65%]"
        data-testid={`rec-card-${rec.id}`}
      >
        {/* Thumbnail */}
        <div className="relative shrink-0 w-20 h-14 rounded-xl overflow-hidden bg-muted">
          <img
            src={thumb}
            alt={rec.title}
            className="w-full h-full object-cover group-hover:brightness-90 transition-all"
            loading="lazy"
          />
          <div className="absolute inset-0 flex items-center justify-center bg-black/25 opacity-0 group-hover:opacity-100 transition-opacity">
            <Play size={14} className="text-white fill-white" />
          </div>
        </div>
        {/* Text */}
        <div className="flex flex-col flex-1 min-w-0 justify-center gap-1">
          <div className="flex items-center gap-1.5">
            <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${rec.categoryStyle}`}>
              {rec.category}
            </span>
            <span className="text-[10px] font-semibold text-primary">阿本推薦</span>
          </div>
          <p className="text-xs font-semibold text-foreground leading-snug line-clamp-2">
            {rec.title}
          </p>
          <p className="text-[11px] text-muted-foreground leading-relaxed line-clamp-2 italic">
            {rec.note}
          </p>
        </div>
      </div>
    </Link>
  );
}

// ── Constants ─────────────────────────────────────────────────
const MAX_CHARS = 1000;
const WARN_THRESHOLD = 800;   // 橘色提示開始
const SOFT_CAP = 950;          // 友善文案切換

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "crisis" | "intercepted";
  content: string;
  timestamp: Date;
  rec?: CourseRec | null;  // optional recommendation attached to assistant messages
}

interface ApiResponse {
  reply: string;
  intercepted: boolean;
  crisis: boolean;
  session_id?: string | null;   // backend echoes session_id back
}

// ── Welcome message ───────────────────────────────────────────
const makeWelcome = (): ChatMessage => ({
  id: "welcome",
  role: "assistant",
  content:
    "你好，我是 Boon。\n\n不管你現在心情如何，我都在這裡陪你。有什麼想說的，或者想聊聊今天的心情嗎？",
  timestamp: new Date(),
});

// ── Generate a stable session ID (React state, not localStorage) ──
const newSessionId = () => crypto.randomUUID();

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([makeWelcome()]);
  const [sessionId, setSessionId] = useState<string>(newSessionId);
  const [input, setInput] = useState("");
  const [darkMode, setDarkMode] = useState(
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
  const [resetConfirm, setResetConfirm] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const resetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  // Track last user text so we can match recommendation after reply arrives
  const lastUserTextRef = useRef<string>("");

  // ── Chat mutation — sends session_id so backend uses server-side memory ──
  const mutation = useMutation({
    mutationFn: async (text: string) => {
      const res = await apiRequest("POST", "/api/v1/chat", {
        message: text,
        session_id: sessionId,
        // history field omitted — backend uses server-side memory when session_id is present
      });
      return res.json() as Promise<ApiResponse>;
    },
    onSuccess: (data) => {
      const role: ChatMessage["role"] = data.crisis
        ? "crisis"
        : data.intercepted
        ? "intercepted"
        : "assistant";

      // Match recommendation from the user's message text
      const rec = role === "assistant"
        ? matchRecommendation(lastUserTextRef.current)
        : null;

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role,
          content: data.reply,
          timestamp: new Date(),
          rec,
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

  // ── Reset mutation — clears server-side memory and starts fresh ──
  const resetMutation = useMutation({
    mutationFn: async () => {
      // Tell backend to clear the current session
      await apiRequest("POST", "/api/v1/reset", { session_id: sessionId });
    },
    onSuccess: () => {
      // Generate a new session ID and reset UI
      setSessionId(newSessionId());
      setMessages([makeWelcome()]);
      setInput("");
      setResetConfirm(false);
    },
    onError: () => {
      // Even if server reset fails, reset client state
      setSessionId(newSessionId());
      setMessages([makeWelcome()]);
      setInput("");
      setResetConfirm(false);
    },
  });

  // Two-step reset: first tap shows confirm state, second tap executes
  const handleResetClick = () => {
    if (resetConfirm) {
      // Confirmed — execute reset
      if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
      resetMutation.mutate();
    } else {
      // First tap — enter confirm state, auto-cancel after 3s
      setResetConfirm(true);
      resetTimerRef.current = setTimeout(() => setResetConfirm(false), 3000);
    }
  };

  const handleSend = () => {
    const text = input.trim();
    if (!text || isOverLimit || mutation.isPending) return;

    // Cancel any pending reset confirm
    if (resetConfirm) {
      setResetConfirm(false);
      if (resetTimerRef.current) clearTimeout(resetTimerRef.current);
    }

    // Store user text for recommendation matching after reply
    lastUserTextRef.current = text;

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

          {/* ── Courses link ── */}
          <Link href="/courses">
            <button
              className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-3 py-1.5 rounded-full border border-border hover:bg-muted"
              aria-label="課程資源"
              data-testid="link-courses"
              title="自學資源 · 影片課堂"
            >
              <BookOpen size={13} />
              <span className="hidden sm:inline">課程資源</span>
            </button>
          </Link>

          {/* ── Reset button ── */}
          <button
            onClick={handleResetClick}
            disabled={resetMutation.isPending || mutation.isPending}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-200 border disabled:opacity-40 disabled:cursor-not-allowed ${
              resetConfirm
                ? "bg-orange-100 dark:bg-orange-950/40 text-orange-700 dark:text-orange-400 border-orange-300 dark:border-orange-700"
                : "bg-muted hover:bg-muted/80 text-muted-foreground border-border"
            }`}
            aria-label={resetConfirm ? "確認開始新對話" : "開始新對話"}
            data-testid="button-reset"
            title={resetConfirm ? "再按一次確認" : "開始新對話（清除記憶）"}
          >
            <RotateCcw
              size={13}
              className={resetMutation.isPending ? "animate-spin" : ""}
            />
            <span>{resetConfirm ? "確認清除？" : "新對話"}</span>
          </button>

          {/* ── Dark mode toggle ── */}
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
              <div key={msg.id}>
                <MessageBubble message={msg} />
                {/* Inline recommendation card — only on assistant replies with a match */}
                {msg.role === "assistant" && msg.rec && (
                  <RecCard rec={msg.rec} />
                )}
              </div>
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
