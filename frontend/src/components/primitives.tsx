import { useMemo } from "react";

import { Icon, type IconName } from "./Icon";

type BadgeKind = "online" | "offline" | "degraded" | "accent" | "amber" | "provisioning" | "error";

export function Badge({ kind = "online", children, dot = true }: { kind?: BadgeKind; children: React.ReactNode; dot?: boolean }) {
  return (
    <span className={`badge ${kind}`}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

export function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return <button className={`toggle ${on ? "on" : ""}`} onClick={onClick} aria-pressed={on} />;
}

interface MonoPillProps {
  children: React.ReactNode;
  accent?: boolean;
  green?: boolean;
  cyan?: boolean;
  cidr?: boolean;
  onClick?: () => void;
}

export function MonoPill({ children, accent, green, cyan, cidr, onClick }: MonoPillProps) {
  const cls = ["mono-pill"];
  if (accent) cls.push("accent");
  if (green) cls.push("green");
  if (cyan) cls.push("cyan");
  if (cidr) cls.push("cidr");
  return (
    <span className={cls.join(" ")} onClick={onClick}>
      {children}
    </span>
  );
}

export function ViaPill({ children }: { children: React.ReactNode }) {
  return <span className="via-pill">{children}</span>;
}

type TileColor = "violet" | "green" | "cyan" | "orange" | "pink" | "amber" | "bg";

interface IconTileProps {
  color?: TileColor;
  size?: "sm" | "md" | "lg";
  icon?: IconName;
  children?: React.ReactNode;
}

export function IconTile({ color = "violet", size = "md", icon, children }: IconTileProps) {
  return (
    <span className={`icon-tile ${size} tile-${color}`}>
      {children ?? (icon && <Icon name={icon} size={size === "lg" ? 20 : size === "sm" ? 14 : 16} />)}
    </span>
  );
}

interface SparklineProps {
  width?: number;
  height?: number;
  color?: string;
  seed?: number;
  fill?: boolean;
}

export function Sparkline({ width = 70, height = 26, color = "currentColor", seed = 1, fill = true }: SparklineProps) {
  const points = useMemo(() => {
    const n = 24;
    let s = seed * 9301 + 49297;
    const rand = () => {
      s = (s * 9301 + 49297) % 233280;
      return s / 233280;
    };
    const arr: number[] = [];
    let v = 0.5;
    for (let i = 0; i < n; i++) {
      v = Math.max(0.05, Math.min(0.95, v + (rand() - 0.5) * 0.4));
      arr.push(v);
    }
    return arr;
  }, [seed]);

  const path = points
    .map((v, i) => {
      const x = (i / (points.length - 1)) * width;
      const y = height - v * height;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const area = `${path} L${width},${height} L0,${height} Z`;
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      {fill && <path d={area} fill={color} opacity="0.18" />}
      <path d={path} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

interface MetricProps {
  label: string;
  value: React.ReactNode;
  sub?: string;
  delta?: string;
  icon?: IconName;
  tileColor?: TileColor;
  sparkSeed?: number;
  showSpark?: boolean;
  sparkColor?: string;
}

export function Metric({
  label,
  value,
  sub,
  delta,
  icon,
  tileColor = "violet",
  sparkSeed = 1,
  showSpark = true,
  sparkColor,
}: MetricProps) {
  return (
    <div className={`metric ${showSpark ? "" : "no-spark"}`}>
      <div className="head">
        <IconTile color={tileColor} icon={icon} />
        <div className="label">{label}</div>
      </div>
      <div className="value">{value}</div>
      <div className="sub">
        {delta && (
          <span className={delta.startsWith("+") ? "delta-up" : delta.startsWith("-") ? "delta-down" : ""}>
            {delta}
          </span>
        )}
        {sub && <span>{sub}</span>}
      </div>
      <div className="spark">
        <Sparkline
          seed={sparkSeed}
          color={sparkColor ?? `var(--${tileColor === "violet" ? "accent" : tileColor})`}
        />
      </div>
    </div>
  );
}

export function SectionHead({
  title,
  count,
  children,
}: {
  title: React.ReactNode;
  count?: number | null;
  children?: React.ReactNode;
}) {
  return (
    <div className="section-head">
      <div className="title">
        {title}
        {count != null && <span className="count-pill">{count}</span>}
      </div>
      <div className="right">{children}</div>
    </div>
  );
}
