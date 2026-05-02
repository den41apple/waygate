import type { ServerSummary } from "../api/types";
import { useWsStore } from "../ws/store";

interface Props {
  server: ServerSummary | null;
}

export function StatusBar({ server }: Props) {
  const wsStatus = useWsStore((state) => state.status);
  return (
    <div className="status-bar">
      <div className="left">
        <span>
          <span className="blink" style={{ color: wsStatus === "connected" ? "var(--green)" : "var(--amber)" }}>
            ●
          </span>{" "}
          ws · {wsStatus}
        </span>
        {server?.last_seen_at && (
          <span>
            last seen <b>{new Date(server.last_seen_at).toLocaleTimeString()}</b>
          </span>
        )}
        {server?.version && <span>agent <b>{server.version}</b></span>}
      </div>
      <div className="right">
        <span>{server?.host ?? "—"}</span>
      </div>
    </div>
  );
}
