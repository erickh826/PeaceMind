import { ChatMessage } from "@/pages/ChatPage";
import BoonLogo from "@/components/BoonLogo";

interface MessageBubbleProps {
  message: ChatMessage;
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const isIntercepted = message.role === "intercepted";

  // Render message text with line breaks
  const renderText = (text: string) =>
    text.split("\n").map((line, i) => (
      <span key={i}>
        {line}
        {i < text.split("\n").length - 1 && <br />}
      </span>
    ));

  if (isUser) {
    return (
      <div
        className="flex justify-end msg-animate"
        data-testid={`msg-user-${message.id}`}
      >
        <div className="max-w-[78%] sm:max-w-[65%]">
          <div className="bg-primary text-primary-foreground rounded-2xl rounded-tr-sm px-4 py-3 text-base leading-relaxed shadow-sm">
            {renderText(message.content)}
          </div>
          <p className="text-xs text-muted-foreground mt-1 text-right pr-1">
            {formatTime(message.timestamp)}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex items-start gap-3 msg-animate"
      data-testid={`msg-boon-${message.id}`}
    >
      {/* Avatar */}
      <div className="shrink-0 mt-1">
        <BoonLogo size={30} />
      </div>

      <div className="max-w-[78%] sm:max-w-[65%]">
        <div
          className={`rounded-2xl rounded-tl-sm px-4 py-3 text-base leading-relaxed shadow-sm ${
            isIntercepted
              ? "bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 text-foreground"
              : "bg-card border border-border text-foreground"
          }`}
        >
          {renderText(message.content)}
        </div>
        <p className="text-xs text-muted-foreground mt-1 pl-1">
          Boon · {formatTime(message.timestamp)}
        </p>
      </div>
    </div>
  );
}

function formatTime(date: Date) {
  return date.toLocaleTimeString("zh-HK", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
