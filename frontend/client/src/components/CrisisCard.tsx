import { Phone, AlertCircle } from "lucide-react";
import BoonLogo from "@/components/BoonLogo";

interface CrisisCardProps {
  content: string;
}

// Parse hotlines from crisis content
const HOTLINES = [
  { name: "生命熱線", number: "2382 0000", tel: "23820000" },
  { name: "撒瑪利亞防止自殺會", number: "2389 2222", tel: "23892222" },
  { name: "醫管局精神健康直通車", number: "18111", tel: "18111" },
];

export default function CrisisCard({ content }: CrisisCardProps) {
  // Extract the personal message (before the hotline section)
  const personalMessage = content.split("**請你現在")[0].trim();

  return (
    <div
      className="flex items-start gap-3 msg-animate"
      data-testid="crisis-card"
      aria-label="危機支援訊息"
    >
      <div className="shrink-0 mt-1">
        <BoonLogo size={30} />
      </div>

      <div className="max-w-[85%] sm:max-w-[70%] space-y-3">
        {/* Personal empathy message */}
        <div className="bg-card border border-border rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm text-base leading-relaxed text-foreground">
          {personalMessage}
        </div>

        {/* Crisis resource card */}
        <div
          className="crisis-card rounded-2xl border-2 border-primary/30 bg-primary/5 dark:bg-primary/10 px-4 py-4 space-y-3"
          role="region"
          aria-label="緊急求助熱線"
        >
          <div className="flex items-center gap-2 text-primary">
            <AlertCircle size={18} strokeWidth={2.5} />
            <span className="font-semibold text-sm">即時求助熱線（24小時）</span>
          </div>

          <div className="space-y-2">
            {HOTLINES.map((line) => (
              <a
                key={line.tel}
                href={`tel:${line.tel}`}
                className="flex items-center justify-between group rounded-xl bg-background border border-border px-3 py-2.5 hover:border-primary/50 hover:bg-primary/5 transition-all duration-200"
                data-testid={`hotline-${line.tel}`}
                aria-label={`${line.name}: ${line.number}`}
              >
                <div>
                  <p className="text-sm font-medium text-foreground">{line.name}</p>
                </div>
                <div className="flex items-center gap-1.5 text-primary font-semibold text-base">
                  <Phone
                    size={15}
                    className="group-hover:scale-110 transition-transform"
                  />
                  <span>{line.number}</span>
                </div>
              </a>
            ))}

            {/* Emergency */}
            <a
              href="tel:999"
              className="flex items-center justify-between group rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 px-3 py-2.5 hover:bg-red-100 dark:hover:bg-red-950/50 transition-all duration-200"
              data-testid="hotline-999"
              aria-label="緊急求助 999"
            >
              <p className="text-sm font-medium text-red-700 dark:text-red-400">
                緊急求助
              </p>
              <div className="flex items-center gap-1.5 text-red-600 dark:text-red-400 font-bold text-base">
                <Phone size={15} />
                <span>999</span>
              </div>
            </a>
          </div>

          <p className="text-xs text-muted-foreground text-center">
            你不孤單。受過訓練的人在電話另一端等你。
          </p>
        </div>
      </div>
    </div>
  );
}
