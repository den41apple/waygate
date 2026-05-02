// SVG-иконки из исходного дизайн-прототипа, перенесены без изменения путей.

export type IconName =
  | "search" | "plus" | "x" | "chevron-right" | "chevron-left" | "settings"
  | "refresh" | "lock" | "globe" | "key" | "terminal" | "filter" | "download"
  | "play" | "check" | "more" | "shield" | "edit" | "alert" | "credit-card"
  | "wallet" | "tunnel" | "route" | "list" | "activity" | "server" | "send"
  | "trending-up" | "trending-down";

interface Props {
  name: IconName;
  size?: number;
}

export function Icon({ name, size = 16 }: Props) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (name) {
    case "search":
      return <svg {...common}><circle cx="11" cy="11" r="7" /><path d="M16 16l4 4" /></svg>;
    case "plus":
      return <svg {...common}><path d="M12 5v14M5 12h14" /></svg>;
    case "x":
      return <svg {...common}><path d="M6 6l12 12M18 6l-12 12" /></svg>;
    case "chevron-right":
      return <svg {...common}><path d="M9 5l7 7-7 7" /></svg>;
    case "chevron-left":
      return <svg {...common}><path d="M15 5l-7 7 7 7" /></svg>;
    case "settings":
      return <svg {...common}><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2" /></svg>;
    case "refresh":
      return <svg {...common}><path d="M21 6v6h-6M3 18v-6h6" /><path d="M5 10a8 8 0 0114-3M19 14a8 8 0 01-14 3" /></svg>;
    case "lock":
      return <svg {...common}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 018 0v4" /></svg>;
    case "globe":
      return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18" /></svg>;
    case "key":
      return <svg {...common}><circle cx="8" cy="12" r="4" /><path d="M12 12h10M18 12v3M22 12v4" /></svg>;
    case "terminal":
      return <svg {...common}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 10l3 2-3 2M13 14h4" /></svg>;
    case "filter":
      return <svg {...common}><path d="M3 4h18l-7 9v6l-4 2v-8z" /></svg>;
    case "download":
      return <svg {...common}><path d="M12 3v12M7 11l5 5 5-5M4 19h16" /></svg>;
    case "play":
      return <svg {...common}><path d="M7 4l13 8-13 8z" fill="currentColor" stroke="none" /></svg>;
    case "check":
      return <svg {...common}><path d="M4 12l5 5 11-11" /></svg>;
    case "more":
      return (
        <svg {...common}>
          <circle cx="5" cy="12" r="1.4" fill="currentColor" />
          <circle cx="12" cy="12" r="1.4" fill="currentColor" />
          <circle cx="19" cy="12" r="1.4" fill="currentColor" />
        </svg>
      );
    case "shield":
      return <svg {...common}><path d="M12 2l8 3v7c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V5z" /></svg>;
    case "edit":
      return <svg {...common}><path d="M16 3l5 5-12 12H4v-5z" /></svg>;
    case "alert":
      return <svg {...common}><path d="M12 3l10 18H2z" /><path d="M12 9v5M12 17v.5" /></svg>;
    case "credit-card":
      return <svg {...common}><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3 10h18" /></svg>;
    case "wallet":
      return <svg {...common}><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3 10h13a3 3 0 010 6H3" /></svg>;
    case "tunnel":
      return <svg {...common}><path d="M3 12c0-5 4-9 9-9s9 4 9 9v9H3z" /><path d="M9 21v-6a3 3 0 016 0v6" /></svg>;
    case "route":
      return <svg {...common}><circle cx="6" cy="19" r="2.5" /><circle cx="18" cy="5" r="2.5" /><path d="M8 19h6a4 4 0 000-8H10a4 4 0 010-8h6" /></svg>;
    case "list":
      return <svg {...common}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>;
    case "activity":
      return <svg {...common}><path d="M3 12h4l3-9 4 18 3-9h4" /></svg>;
    case "server":
      return (
        <svg {...common}>
          <rect x="3" y="4" width="18" height="7" rx="2" />
          <rect x="3" y="13" width="18" height="7" rx="2" />
          <circle cx="7" cy="7.5" r="0.5" fill="currentColor" />
          <circle cx="7" cy="16.5" r="0.5" fill="currentColor" />
        </svg>
      );
    case "send":
      return <svg {...common}><path d="M22 2L11 13M22 2l-7 20-4-9-9-4z" /></svg>;
    case "trending-up":
      return <svg {...common}><path d="M3 17l6-6 4 4 8-8M14 7h7v7" /></svg>;
    case "trending-down":
      return <svg {...common}><path d="M3 7l6 6 4-4 8 8M14 17h7v-7" /></svg>;
    default:
      return null;
  }
}
