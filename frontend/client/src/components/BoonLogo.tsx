/**
 * Boon SVG Logo — a soft leaf/heart form representing calm and growth
 * Works at 24px and 200px. Uses currentColor for theme adaptation.
 */
interface BoonLogoProps {
  size?: number;
  className?: string;
}

export default function BoonLogo({ size = 40, className = "" }: BoonLogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="PeaceMind Boon logo"
      className={className}
      role="img"
    >
      {/* Soft circle background */}
      <circle cx="20" cy="20" r="20" fill="hsl(192 45% 40%)" opacity="0.12" />
      {/* Leaf / peace drop shape */}
      <path
        d="M20 8C20 8 12 13 12 21C12 25.4183 15.5817 29 20 29C24.4183 29 28 25.4183 28 21C28 13 20 8 20 8Z"
        fill="hsl(192 45% 40%)"
        opacity="0.9"
      />
      {/* Center vertical line — grounding */}
      <line
        x1="20" y1="22" x2="20" y2="32"
        stroke="hsl(192 45% 40%)"
        strokeWidth="2"
        strokeLinecap="round"
        opacity="0.7"
      />
      {/* Small horizontal branch */}
      <line
        x1="16" y1="27" x2="24" y2="27"
        stroke="hsl(192 45% 40%)"
        strokeWidth="1.5"
        strokeLinecap="round"
        opacity="0.5"
      />
    </svg>
  );
}
