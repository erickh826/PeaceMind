import BoonLogo from "@/components/BoonLogo";

export default function TypingIndicator() {
  return (
    <div
      className="flex items-start gap-3 msg-animate"
      aria-label="Boon 正在輸入"
      aria-live="polite"
      data-testid="typing-indicator"
    >
      <div className="shrink-0 mt-1">
        <BoonLogo size={30} />
      </div>
      <div className="bg-card border border-border rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
        <div className="flex items-center gap-1.5 h-5">
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="typing-dot" />
        </div>
      </div>
    </div>
  );
}
