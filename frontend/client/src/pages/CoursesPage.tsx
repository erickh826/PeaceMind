/**
 * CoursesPage — 自學資源 · 影片課堂
 *
 * Design reference: jcthplus.org/courses
 * - Warm cream background (matches existing palette)
 * - Coloured category pill tags
 * - YouTube embed cards with title + description + duration
 * - Recommendation card (matches the chat recommendation UI in screenshot)
 * - Responsive 3-col → 2-col → 1-col grid
 */

import { useState } from "react";
import { Link } from "wouter";
import BoonLogo from "@/components/BoonLogo";
import { Moon, Sun, ArrowLeft, Play, Clock, Tag } from "lucide-react";
import { useEffect } from "react";

// ── Category tag colours (mirrors jcthplus pill tags) ──────────────────────
const CATEGORY_STYLES: Record<string, string> = {
  "靜觀冥想":   "bg-teal-100   text-teal-700   dark:bg-teal-900/40  dark:text-teal-300",
  "情緒管理":   "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300",
  "大腦科學":   "bg-blue-100   text-blue-700   dark:bg-blue-900/40   dark:text-blue-300",
  "自我提升":   "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  "正向思維":   "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
  "人際關係":   "bg-pink-100   text-pink-700   dark:bg-pink-900/40   dark:text-pink-300",
  "內在療癒":   "bg-green-100  text-green-700  dark:bg-green-900/40  dark:text-green-300",
};

// ── Course data ─────────────────────────────────────────────────────────────
interface Course {
  id: string;
  title: string;
  description: string;
  youtubeId: string;
  category: string;
  duration: string;
  recommended?: boolean;       // shown in the "阿本推薦" recommendation card
  recommendNote?: string;      // note from Boon (shown in recommendation card)
}

const COURSES: Course[] = [
  {
    id: "mindful-3min",
    title: "【靜觀冥想】靜觀三分鐘",
    description:
      "只需三分鐘，帶你進入靜觀狀態。適合工作壓力大、心緒不寧的時候，隨時隨地重設一下自己。",
    youtubeId: "846VoF-JPno",
    category: "靜觀冥想",
    duration: "3 分鐘",
    recommended: true,
    recommendNote:
      "當你感到被工作「捽」到喘不過氣，三分鐘靜觀可以幫你重拾一點喘息空間。",
  },
  {
    id: "anxiety-relief",
    title: "當焦慮感來襲，心理師教你這樣做",
    description:
      "心理師親自示範面對焦慮的具體步驟，幫助你在焦慮湧現時找回掌控感，而不是被情緒淹沒。",
    youtubeId: "bY4Ux7K-LAs",
    category: "情緒管理",
    duration: "約 8 分鐘",
    recommended: true,
    recommendNote:
      "如果你分不清「實務性擔心」和「假設性擔心」，這個影片可以幫你理清思路，搵返掌控感。",
  },
  {
    id: "brain-emotion",
    title: "如何管理情緒？從大腦科學的角度來看",
    description:
      "從神經科學角度解釋情緒的來源，讓你更了解自己的反應，學會科學地管理情緒而不是強行壓抑。",
    youtubeId: "27zBdxVGBOU",
    category: "大腦科學",
    duration: "約 12 分鐘",
  },
  {
    id: "procrastination",
    title: "三步驟教你改善拖延症！",
    description:
      "拖延不是懶，而是情緒調節問題。三個實用步驟，幫你打破拖延循環，重新掌握自己的時間。",
    youtubeId: "SVP10jrnkb0",
    category: "自我提升",
    duration: "約 7 分鐘",
  },
  {
    id: "affirmations",
    title: "每日 10 分鐘 廣東話肯定句",
    description:
      "用粵語肯定句建立正向思維習慣。每天十分鐘，溫柔地與自己對話，強化自我價值感。",
    youtubeId: "-2KNgktM6Vo",
    category: "正向思維",
    duration: "10 分鐘",
  },
  {
    id: "love-boundary",
    title: "沉船其實不是愛",
    description:
      "探討健康與不健康的愛情模式，幫助你辨識關係中的邊界，學懂愛自己才是真正的愛。",
    youtubeId: "ys7gELVX174",
    category: "人際關係",
    duration: "約 15 分鐘",
  },
  {
    id: "inner-child",
    title: "療癒你的內在小孩",
    description:
      "內在小孩的創傷如何影響成年後的行為與關係？透過引導練習，開始與自己的內在小孩和解。",
    youtubeId: "rJ-2DXLVDEY",
    category: "內在療癒",
    duration: "約 20 分鐘",
    recommended: true,
    recommendNote:
      "長期處於「烤多士」狀態的人，內在往往有一個很累的小孩。這個影片可以幫你跟自己重新連結。",
  },
];

const ALL_CATEGORIES = ["全部", ...Object.keys(CATEGORY_STYLES)];

// ── YouTube thumbnail helper ─────────────────────────────────────────────────
const ytThumb = (id: string) =>
  `https://img.youtube.com/vi/${id}/mqdefault.jpg`;

// ── Course Card ─────────────────────────────────────────────────────────────
function CourseCard({ course, onPlay }: { course: Course; onPlay: () => void }) {
  const tagStyle = CATEGORY_STYLES[course.category] ?? "bg-gray-100 text-gray-600";

  return (
    <div className="group flex flex-col rounded-2xl overflow-hidden border border-border bg-card shadow-sm hover:shadow-md transition-all duration-200">
      {/* Thumbnail */}
      <div className="relative cursor-pointer" onClick={onPlay}>
        <img
          src={ytThumb(course.youtubeId)}
          alt={course.title}
          className="w-full aspect-video object-cover bg-muted group-hover:brightness-90 transition-all duration-200"
          loading="lazy"
        />
        {/* Play overlay */}
        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity duration-200">
          <div className="bg-black/60 rounded-full p-4">
            <Play size={28} className="text-white fill-white" />
          </div>
        </div>
        {/* Duration badge */}
        <div className="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-2 py-0.5 rounded-md flex items-center gap-1">
          <Clock size={10} />
          {course.duration}
        </div>
        {/* Recommended badge */}
        {course.recommended && (
          <div className="absolute top-2 left-2 bg-primary text-primary-foreground text-xs px-2 py-0.5 rounded-full font-medium">
            阿本推薦
          </div>
        )}
      </div>

      {/* Content */}
      <div className="flex flex-col flex-1 p-4 gap-2">
        {/* Category tag */}
        <span className={`self-start text-xs font-medium px-2.5 py-0.5 rounded-full ${tagStyle}`}>
          {course.category}
        </span>
        <h3 className="font-semibold text-sm text-foreground leading-snug line-clamp-2">
          {course.title}
        </h3>
        <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3 flex-1">
          {course.description}
        </p>

        {/* Boon recommendation note */}
        {course.recommended && course.recommendNote && (
          <div className="mt-1 rounded-xl bg-accent/60 border border-accent px-3 py-2">
            <p className="text-xs text-accent-foreground leading-relaxed">
              <span className="font-semibold text-primary">阿本：</span>
              {course.recommendNote}
            </p>
          </div>
        )}

        {/* Watch button */}
        <button
          onClick={onPlay}
          className="mt-2 w-full rounded-xl bg-primary text-primary-foreground text-sm font-medium py-2 hover:opacity-90 active:scale-[.98] transition-all duration-150"
        >
          觀看
        </button>
      </div>
    </div>
  );
}

// ── Video Modal ─────────────────────────────────────────────────────────────
function VideoModal({ course, onClose }: { course: Course; onClose: () => void }) {
  // Close on backdrop click
  const handleBackdrop = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  };
  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const tagStyle = CATEGORY_STYLES[course.category] ?? "bg-gray-100 text-gray-600";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={handleBackdrop}
    >
      <div className="relative w-full max-w-3xl bg-card rounded-2xl overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-200">
        {/* YouTube embed */}
        <div className="aspect-video w-full bg-black">
          <iframe
            src={`https://www.youtube.com/embed/${course.youtubeId}?autoplay=1&rel=0`}
            title={course.title}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
            className="w-full h-full"
          />
        </div>
        {/* Info */}
        <div className="p-5">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1">
              <span className={`inline-block text-xs font-medium px-2.5 py-0.5 rounded-full mb-2 ${tagStyle}`}>
                {course.category}
              </span>
              <h2 className="font-semibold text-base text-foreground leading-snug">
                {course.title}
              </h2>
              <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
                {course.description}
              </p>
            </div>
            <button
              onClick={onClose}
              className="shrink-0 p-2 rounded-full hover:bg-muted transition-colors text-muted-foreground"
              aria-label="關閉"
            >
              ✕
            </button>
          </div>
          {course.recommended && course.recommendNote && (
            <div className="mt-3 rounded-xl bg-accent/60 border border-accent px-3 py-2">
              <p className="text-xs text-accent-foreground leading-relaxed">
                <span className="font-semibold text-primary">阿本推薦：</span>
                {course.recommendNote}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────
export default function CoursesPage() {
  const [darkMode, setDarkMode] = useState(
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
  const [activeCategory, setActiveCategory] = useState("全部");
  const [playingCourse, setPlayingCourse] = useState<Course | null>(null);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", darkMode);
  }, [darkMode]);

  const filtered =
    activeCategory === "全部"
      ? COURSES
      : COURSES.filter((c) => c.category === activeCategory);

  const recommended = COURSES.filter((c) => c.recommended);

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* ── Header ── */}
      <header className="flex items-center justify-between px-4 py-3 border-b border-border bg-card/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <BoonLogo size={32} />
          <div>
            <h1 className="font-semibold text-base text-foreground leading-tight">
              Boon
            </h1>
            <p className="text-xs text-muted-foreground">心理健康支持助理</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* Back to chat */}
          <Link href="/">
            <button className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-3 py-1.5 rounded-full border border-border hover:bg-muted">
              <ArrowLeft size={13} />
              返回聊天
            </button>
          </Link>
          <button
            onClick={() => setDarkMode((d) => !d)}
            className="p-2 rounded-full hover:bg-muted transition-colors"
            aria-label={darkMode ? "切換淺色模式" : "切換深色模式"}
          >
            {darkMode ? (
              <Sun size={18} className="text-muted-foreground" />
            ) : (
              <Moon size={18} className="text-muted-foreground" />
            )}
          </button>
        </div>
      </header>

      {/* ── Hero banner ── */}
      <div className="bg-gradient-to-br from-amber-50 via-orange-50 to-teal-50 dark:from-slate-900 dark:via-slate-800 dark:to-slate-900 border-b border-border">
        <div className="max-w-4xl mx-auto px-6 py-10 flex flex-col sm:flex-row items-center gap-6">
          {/* Illustration placeholder — warm circle with emoji */}
          <div className="w-28 h-28 shrink-0 rounded-full bg-gradient-to-br from-amber-200 to-orange-300 dark:from-amber-800 dark:to-orange-900 flex items-center justify-center text-5xl shadow-md select-none">
            🎬
          </div>
          <div>
            <h2 className="text-2xl font-bold text-foreground mb-2">自學資源 · 影片課堂</h2>
            <p className="text-sm text-muted-foreground leading-relaxed max-w-xl">
              由心理學家和心理師精選的影片，涵蓋靜觀冥想、情緒管理、自我成長等主題。
              阿本會根據你的對話，為你推薦最適合的影片。
            </p>
            <div className="flex flex-wrap gap-3 mt-4 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5 bg-white/70 dark:bg-slate-800/70 px-3 py-1.5 rounded-full border border-border">
                <span className="text-primary">▶</span> 免費觀看
              </span>
              <span className="flex items-center gap-1.5 bg-white/70 dark:bg-slate-800/70 px-3 py-1.5 rounded-full border border-border">
                <span className="text-primary">✦</span> 阿本個人化推薦
              </span>
              <span className="flex items-center gap-1.5 bg-white/70 dark:bg-slate-800/70 px-3 py-1.5 rounded-full border border-border">
                <span className="text-primary">◎</span> 隨時隨地學習
              </span>
            </div>
          </div>
        </div>
      </div>

      <main className="flex-1 max-w-5xl mx-auto w-full px-4 py-8">

        {/* ── Boon Recommends section ── */}
        {recommended.length > 0 && (
          <section className="mb-10">
            <div className="flex items-center gap-2 mb-4">
              <div className="w-7 h-7 rounded-full bg-primary flex items-center justify-center">
                <span className="text-white text-xs font-bold">阿</span>
              </div>
              <h3 className="font-semibold text-base text-foreground">阿本今日推薦</h3>
              <span className="text-xs text-muted-foreground ml-1">· 根據常見對話主題為你精選</span>
            </div>

            {/* Horizontal scroll on mobile, 3-col on desktop */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {recommended.map((course) => (
                <div
                  key={course.id}
                  className="flex gap-3 p-3 rounded-2xl border border-border bg-card hover:shadow-md transition-all duration-200 cursor-pointer"
                  onClick={() => setPlayingCourse(course)}
                >
                  {/* Thumbnail small */}
                  <div className="relative shrink-0 w-24 h-16 rounded-xl overflow-hidden bg-muted">
                    <img
                      src={ytThumb(course.youtubeId)}
                      alt={course.title}
                      className="w-full h-full object-cover"
                      loading="lazy"
                    />
                    <div className="absolute inset-0 flex items-center justify-center bg-black/20 opacity-0 hover:opacity-100 transition-opacity">
                      <Play size={16} className="text-white fill-white" />
                    </div>
                  </div>
                  <div className="flex flex-col flex-1 min-w-0">
                    <span className={`self-start text-xs font-medium px-2 py-0.5 rounded-full mb-1 ${CATEGORY_STYLES[course.category] ?? ""}`}>
                      {course.category}
                    </span>
                    <p className="text-xs font-semibold text-foreground leading-snug line-clamp-2 flex-1">
                      {course.title}
                    </p>
                    {course.recommendNote && (
                      <p className="text-xs text-muted-foreground mt-1 line-clamp-1 italic">
                        {course.recommendNote}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ── Browse all section ── */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-semibold text-base text-foreground">瀏覽全部影片</h3>
            <span className="text-xs text-muted-foreground">{filtered.length} 個影片</span>
          </div>

          {/* Category filter pills */}
          <div className="flex flex-wrap gap-2 mb-6">
            {ALL_CATEGORIES.map((cat) => {
              const isActive = activeCategory === cat;
              const tagStyle = CATEGORY_STYLES[cat];
              return (
                <button
                  key={cat}
                  onClick={() => setActiveCategory(cat)}
                  className={`flex items-center gap-1 text-xs font-medium px-3 py-1.5 rounded-full border transition-all duration-150 ${
                    isActive
                      ? "bg-primary text-primary-foreground border-primary shadow-sm"
                      : tagStyle
                      ? `${tagStyle} border-transparent hover:opacity-80`
                      : "bg-muted text-muted-foreground border-border hover:bg-secondary"
                  }`}
                >
                  {cat !== "全部" && <Tag size={10} />}
                  {cat}
                </button>
              );
            })}
          </div>

          {/* Course grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {filtered.map((course) => (
              <CourseCard
                key={course.id}
                course={course}
                onPlay={() => setPlayingCourse(course)}
              />
            ))}
          </div>

          {filtered.length === 0 && (
            <div className="py-16 text-center text-muted-foreground text-sm">
              暫時沒有這個類別的影片
            </div>
          )}
        </section>
      </main>

      {/* ── Back to chat CTA ── */}
      <div className="border-t border-border bg-card/80 py-6 px-4 text-center">
        <p className="text-sm text-muted-foreground mb-3">
          看完影片，想聊聊你的感受嗎？
        </p>
        <Link href="/">
          <button className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-6 py-2.5 rounded-xl text-sm font-medium hover:opacity-90 transition-opacity">
            <span>返回與阿本聊天</span>
            <span>→</span>
          </button>
        </Link>
      </div>

      {/* ── Video modal ── */}
      {playingCourse && (
        <VideoModal
          course={playingCourse}
          onClose={() => setPlayingCourse(null)}
        />
      )}
    </div>
  );
}
