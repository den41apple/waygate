import { useState } from "react";

import { useLogin } from "../api/auth";
import { ApiError } from "../api/client";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();

  const onSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!username || !password) return;
    login.mutate({ username, password });
  };

  const errorMessage = login.error instanceof ApiError && login.error.status === 401
    ? "Неверный логин или пароль"
    : login.error
    ? "Не удалось войти, попробуйте снова"
    : null;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100vh",
        background: "var(--bg-0, #0d0d12)",
      }}
    >
      <form
        onSubmit={onSubmit}
        style={{
          width: 360,
          padding: 28,
          background: "var(--bg-1)",
          border: "1px solid var(--border)",
          borderRadius: "var(--r)",
          boxShadow: "0 12px 40px rgba(0,0,0,0.35)",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <IconTile color="violet" icon="lock" size="lg" />
          <div>
            <div style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.01em" }}>Waygate</div>
            <div style={{ fontSize: 12, color: "var(--text-3)" }}>Вход в панель управления</div>
          </div>
        </div>

        <div className="field">
          <label>Логин</label>
          <input
            className="input"
            value={username}
            autoFocus
            autoComplete="username"
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>

        <div className="field">
          <label>Пароль</label>
          <input
            className="input"
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>

        {errorMessage && (
          <div
            style={{
              fontSize: 12,
              color: "var(--red, #ef4444)",
              padding: "8px 12px",
              background: "var(--red-tint, rgba(239,68,68,0.12))",
              borderRadius: 8,
            }}
          >
            {errorMessage}
          </div>
        )}

        <button
          type="submit"
          className="btn primary"
          disabled={login.isPending || !username || !password}
        >
          {login.isPending ? "Вхожу…" : "Войти"} <Icon name="chevron-right" size={14} />
        </button>

        <div style={{ fontSize: 11, color: "var(--text-3)", textAlign: "center" }}>
          Первый админ создаётся через ENV{" "}
          <span className="mono">WAYGATE_ADMIN_USER</span> /{" "}
          <span className="mono">WAYGATE_ADMIN_PASSWORD</span>.
        </div>
      </form>
    </div>
  );
}
