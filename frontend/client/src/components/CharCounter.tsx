/**
 * CharCounter — Empathetic UI 字數計數器
 * 三個狀態：
 *   - 正常 (< warn):    淡色靜默顯示
 *   - 提醒 (warn~max):  橘色，友善提示
 *   - 達限 (= max):     深橘，「現在送出剛剛好」
 */

interface CharCounterProps {
  count: number;
  max: number;
  warn: number;
}

export default function CharCounter({ count, max, warn }: CharCounterProps) {
  const remaining = max - count;
  const isWarning = count >= warn && count <= max;
  const isOver = count > max;

  // Arc progress (SVG circle)
  const radius = 10;
  const circumference = 2 * Math.PI * radius;
  const progress = Math.min(count / max, 1);
  const strokeDashoffset = circumference * (1 - progress);

  const strokeColor = isOver
    ? "#C2410C"
    : isWarning
    ? "#EA580C"
    : "hsl(192 45% 40%)";

  if (count === 0) return null;

  return (
    <div
      className="flex items-center gap-1 shrink-0"
      aria-label={`已輸入 ${count} 字，上限 ${max} 字`}
      data-testid="char-counter"
    >
      {/* Mini arc indicator */}
      <svg width="24" height="24" className="-rotate-90" aria-hidden="true">
        {/* Track */}
        <circle
          cx="12" cy="12" r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="text-border"
        />
        {/* Progress */}
        <circle
          cx="12" cy="12" r={radius}
          fill="none"
          stroke={strokeColor}
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          style={{ transition: "stroke-dashoffset 0.15s ease, stroke 0.15s ease" }}
        />
      </svg>

      {/* Numeric remaining — only show when warning */}
      {isWarning && (
        <span
          className={`text-xs font-medium tabular-nums transition-colors ${
            isOver ? "char-danger-text" : "char-warning-text"
          }`}
          aria-live="polite"
        >
          {remaining}
        </span>
      )}
    </div>
  );
}
