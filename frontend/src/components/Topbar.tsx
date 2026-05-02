import { useLogout } from "../api/auth";
import { useRefreshServer, useRotateToken } from "../api/servers";
import type { ServerSummary } from "../api/types";
import { useAuthStore } from "../store/auth";
import { useUiStore } from "../store/ui";
import { Icon } from "./Icon";
import { Badge } from "./primitives";

interface Props {
  server: ServerSummary | null;
  onTLS: () => void;
}

export function Topbar({ server, onTLS }: Props) {
  const refresh = useRefreshServer();
  const rotateToken = useRotateToken();
  const logout = useLogout();
  const user = useAuthStore((state) => state.user);
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);
  const showSparklines = useUiStore((state) => state.showSparklines);
  const setShowSparklines = useUiStore((state) => state.setShowSparklines);

  if (!server) {
    return (
      <div className="topbar">
        <div className="tb-title">Waygate</div>
        <div className="tb-spacer" />
        <div className="tb-actions">
          {user && <span style={{ fontSize: 12, color: "var(--text-3)" }}>{user.username}</span>}
          <button
            className="tb-btn"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
            title="Выйти из панели"
          >
            <Icon name="x" size={14} /> Выход
          </button>
        </div>
      </div>
    );
  }

  const onRotate = () => {
    if (!window.confirm("Сгенерировать новый Bearer-токен агента? Старый сразу станет невалидным.")) return;
    rotateToken.mutate(server.id);
  };

  return (
    <div className="topbar">
      <div className="tb-title">{server.name}</div>
      <div className="tb-ip">{server.host}:{server.port}</div>
      <Badge kind={server.status === "online" ? "online" : server.status === "degraded" ? "degraded" : "offline"}>
        {server.status}
      </Badge>
      {server.awg_containers.length > 0 && <Badge kind="accent">awg · {server.awg_containers.length}</Badge>}
      <div className="tb-spacer" />
      <div className="tb-actions">
        <button
          className="tb-btn"
          title={theme === "dark" ? "Переключить на светлую тему" : "Переключить на тёмную тему"}
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        >
          {theme === "dark" ? "☾" : "☀"}
        </button>
        <button
          className="tb-btn"
          title={showSparklines ? "Скрыть спарклайны" : "Показать спарклайны"}
          onClick={() => setShowSparklines(!showSparklines)}
        >
          <Icon name="activity" size={14} /> {showSparklines ? "spark on" : "spark off"}
        </button>
        <button className="tb-btn" onClick={onTLS}><Icon name="lock" size={14} /> TLS</button>
        <button
          className="tb-btn"
          onClick={onRotate}
          disabled={rotateToken.isPending}
          title="Ротация Bearer-токена агента"
        >
          <Icon name="key" size={14} /> {rotateToken.isPending ? "Ротация…" : "Токен"}
        </button>
        <button
          className="tb-btn"
          onClick={() => refresh.mutate(server.id)}
          disabled={refresh.isPending}
        >
          <Icon name="refresh" size={14} /> {refresh.isPending ? "Обновляю…" : "Обновить"}
        </button>
        {user && (
          <span
            style={{ fontSize: 12, color: "var(--text-3)", padding: "0 6px" }}
            title={user.is_admin ? "admin" : "user"}
          >
            {user.username}
          </span>
        )}
        <button
          className="tb-btn"
          onClick={() => logout.mutate()}
          disabled={logout.isPending}
          title="Выйти из панели"
        >
          <Icon name="x" size={14} /> Выход
        </button>
      </div>
    </div>
  );
}
