import { useRef, useState } from "react";

import { useMetrics } from "../api/metrics";
import type { MetricsPoint, MetricsRange } from "../api/types";
import { Metric, SectionHead } from "../components/primitives";

interface Props {
  serverId: number;
  showSpark: boolean;
}

const RANGES: MetricsRange[] = ["1h", "6h", "24h"];

function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatRelativeTime(point: MetricsPoint, now: Date): string {
  const ts = new Date(point.timestamp).getTime();
  const diffSeconds = Math.max(0, Math.round((now.getTime() - ts) / 1000));
  const minutes = Math.floor(diffSeconds / 60);
  const seconds = diffSeconds % 60;
  return `-${minutes}:${seconds.toString().padStart(2, "0")}`;
}

interface ChartProps {
  title: string;
  subtitle: string;
  color: string;
  points: MetricsPoint[];
  field: "rx_bytes" | "tx_bytes";
  hoverIndex: number | null;
  onHoverChange: (index: number | null) => void;
}

function Chart({ title, subtitle, color, points, field, hoverIndex, onHoverChange }: ChartProps) {
  const W = 1100;
  const H = 200;
  const P = { l: 50, r: 14, t: 12, b: 26 };
  const innerW = W - P.l - P.r;
  const innerH = H - P.t - P.b;
  const svgRef = useRef<SVGSVGElement | null>(null);

  if (points.length < 2) {
    return (
      <div className="chart-card">
        <div className="chart-head">
          <div className="title">{title}</div>
          <div className="legend"><span>нет данных за выбранный диапазон</span></div>
        </div>
      </div>
    );
  }

  const values = points.map((point) => point[field]);
  const max = Math.max(...values, 1);
  const xAt = (index: number) => P.l + (index / (points.length - 1)) * innerW;
  const yAt = (value: number) => P.t + innerH - (value / max) * innerH;

  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${xAt(index).toFixed(1)},${yAt(point[field]).toFixed(1)}`)
    .join(" ");
  const area = `${path} L${P.l + innerW},${P.t + innerH} L${P.l},${P.t + innerH} Z`;

  const peak = Math.max(...values);
  const avg = values.reduce((acc, value) => acc + value, 0) / values.length;

  const handleMouseMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (svg === null) return;
    const rect = svg.getBoundingClientRect();
    // Маппим клиентский X в координаты viewBox (preserveAspectRatio=none → линейно).
    const xInViewbox = ((event.clientX - rect.left) / rect.width) * W;
    const xInPlot = Math.max(0, Math.min(innerW, xInViewbox - P.l));
    const fractional = xInPlot / innerW;
    const index = Math.round(fractional * (points.length - 1));
    const clamped = Math.max(0, Math.min(points.length - 1, index));
    onHoverChange(clamped);
  };

  const activePoint = hoverIndex !== null ? points[hoverIndex] : null;
  const activeValue = activePoint ? activePoint[field] : 0;
  const tooltipLeftPct = hoverIndex !== null ? (xAt(hoverIndex) / W) * 100 : 0;
  const now = new Date();

  return (
    <div className="chart-card">
      <div className="chart-head">
        <div className="title">{title}</div>
        <div className="legend">
          <span><span className="swatch" style={{ background: color }} /> bytes/s window</span>
          <span>peak <b>{formatBytes(peak)}</b></span>
          <span>avg <b>{formatBytes(avg)}</b></span>
        </div>
      </div>
      <div className="chart-plot" style={{ position: "relative" }}>
        <svg
          ref={svgRef}
          width="100%"
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          style={{ display: "block" }}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => onHoverChange(null)}
        >
          <defs>
            <linearGradient id={`g-${field}`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.32" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#g-${field})`} />
          <path d={path} fill="none" stroke={color} strokeWidth="2" />
          {hoverIndex !== null && activePoint && (
            <>
              <line
                x1={xAt(hoverIndex)}
                x2={xAt(hoverIndex)}
                y1={P.t}
                y2={P.t + innerH}
                stroke={color}
                strokeWidth="1"
                strokeDasharray="3 4"
                opacity="0.6"
                vectorEffect="non-scaling-stroke"
              />
              <circle
                cx={xAt(hoverIndex)}
                cy={yAt(activeValue)}
                r="4"
                fill="var(--card, #0e1015)"
                stroke={color}
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            </>
          )}
        </svg>
        {hoverIndex !== null && activePoint && (
          <div
            className="chart-tooltip"
            style={{
              left: `${tooltipLeftPct}%`,
              // По дизайну — карточка справа от точки. На правом краю прижимаем к курсору слева.
              transform: tooltipLeftPct > 75 ? "translate(-100%, 0)" : "translate(12px, 0)",
              borderLeftColor: color,
            }}
          >
            <div className="t-time">{formatRelativeTime(activePoint, now)}</div>
            <div className="t-value" style={{ color }}>
              {formatBytes(activeValue)}/s
            </div>
            <div className="t-sub">
              {subtitle} · {formatBytes(activeValue)}/c
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function MetricsTab({ serverId, showSpark }: Props) {
  const [range, setRange] = useState<MetricsRange>("6h");
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const { data, isLoading } = useMetrics(serverId, range);
  const points = data?.points ?? [];

  const latest = points.at(-1);
  const previous = points.at(-2);
  const rxDelta = latest && previous ? latest.rx_bytes - previous.rx_bytes : 0;
  const txDelta = latest && previous ? latest.tx_bytes - previous.tx_bytes : 0;

  return (
    <>
      <div className="metric-row">
        <Metric label="RX последний" value={latest ? formatBytes(latest.rx_bytes) : "—"} delta={rxDelta ? `+${formatBytes(rxDelta)}` : undefined} sub="snapshot" icon="trending-down" tileColor="cyan" sparkSeed={21} showSpark={showSpark} />
        <Metric label="TX последний" value={latest ? formatBytes(latest.tx_bytes) : "—"} delta={txDelta ? `+${formatBytes(txDelta)}` : undefined} sub="snapshot" icon="trending-up" tileColor="violet" sparkSeed={23} showSpark={showSpark} />
        <Metric label="Точек в окне" value={points.length} sub={`за ${range}`} icon="activity" tileColor="green" sparkSeed={25} showSpark={showSpark} />
        <Metric label="Server ID" value={serverId} sub="agent_metrics" icon="server" tileColor="orange" sparkSeed={27} showSpark={showSpark} />
      </div>

      <div className="section-head">
        <div className="title">Сетевая пропускная</div>
        <div className="right">
          <div className="range-switch">
            {RANGES.map((rangeOption) => (
              <button
                key={rangeOption}
                className={range === rangeOption ? "active" : ""}
                onClick={() => setRange(rangeOption)}
              >
                {rangeOption}
              </button>
            ))}
          </div>
        </div>
      </div>

      {isLoading
        ? <div className="hint">Загружаю метрики…</div>
        : (
          <>
            <Chart
              title="RX bytes/s"
              subtitle="received"
              color="#22d3ee"
              points={points}
              field="rx_bytes"
              hoverIndex={hoverIndex}
              onHoverChange={setHoverIndex}
            />
            <Chart
              title="TX bytes/s"
              subtitle="transmitted"
              color="#a78bfa"
              points={points}
              field="tx_bytes"
              hoverIndex={hoverIndex}
              onHoverChange={setHoverIndex}
            />
          </>
        )}

      <SectionHead title="Источник данных" count={null}>
        <span style={{ fontSize: 11, color: "var(--text-3)" }}>
          обновляется каждые 30 сек через WebSocket / refetch
        </span>
      </SectionHead>
    </>
  );
}
