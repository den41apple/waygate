import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { fetchWsToken } from "../api/auth";
import { awgClientsKey } from "../api/awgClients";
import { dnsKey } from "../api/dns";
import { GEOIP_KEY } from "../api/geoip";
import { rulesKey } from "../api/rules";
import { SERVERS_KEY } from "../api/servers";
import { tlsKey } from "../api/tls";
import type { WsEvent } from "../api/types";
import { useWsStore } from "./store";

const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 15_000;

/**
 * Один общий /ws/events на всё приложение.
 *
 * - Получает короткоживущий JWT через POST /api/v1/auth/ws-token
 * - Авто-реконнект с экспоненциальным бэкоффом
 * - По каждому WsEvent инвалидирует соответствующие TanStack-кеши,
 *   так что UI обновляется без ручных refetch.
 */
export function useWebSocket(): void {
  const queryClient = useQueryClient();
  const setStatus = useWsStore((state) => state.setStatus);
  const socketRef = useRef<WebSocket | null>(null);
  const cancelledRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);

  useEffect(() => {
    cancelledRef.current = false;

    const handleEvent = (event: WsEvent) => {
      switch (event.type) {
        case "server.created":
        case "server.deleted":
        case "server.status_changed":
        case "server.agent_updated":
          queryClient.invalidateQueries({ queryKey: SERVERS_KEY });
          break;
        case "server.metrics":
          // metrics-кеш сам обновляется по refetchInterval — здесь только sidebar
          queryClient.invalidateQueries({ queryKey: SERVERS_KEY });
          break;
        case "rule.applied":
          if (event.server_id != null) {
            queryClient.invalidateQueries({ queryKey: rulesKey(event.server_id) });
          }
          break;
        case "dns.applied":
          if (event.server_id != null) {
            queryClient.invalidateQueries({ queryKey: dnsKey(event.server_id) });
          }
          break;
        case "geoip.synced":
          queryClient.invalidateQueries({ queryKey: GEOIP_KEY });
          break;
        case "tls.applied":
          if (event.server_id != null) {
            queryClient.invalidateQueries({ queryKey: tlsKey(event.server_id) });
          }
          break;
        case "provision.progress":
          // SSE-стрим уже доносит детали в AddServerModal; здесь просто пинг
          queryClient.invalidateQueries({ queryKey: SERVERS_KEY });
          break;
        case "awg_client.created":
        case "awg_client.deleted":
        case "awg_client.status_changed":
          if (event.server_id != null) {
            queryClient.invalidateQueries({ queryKey: awgClientsKey(event.server_id) });
          }
          break;
        default:
          break;
      }
    };

    const connect = async (): Promise<void> => {
      if (cancelledRef.current) return;
      setStatus("connecting");
      let token: string;
      try {
        const tokenResponse = await fetchWsToken();
        token = tokenResponse.token;
      } catch (error) {
        console.warn("ws: не удалось получить токен:", error);
        scheduleReconnect();
        return;
      }
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${protocol}://${window.location.host}/ws/events?token=${encodeURIComponent(token)}`;
      const socket = new WebSocket(url);
      socketRef.current = socket;
      socket.onopen = () => {
        reconnectAttemptsRef.current = 0;
        setStatus("connected");
      };
      socket.onmessage = (message) => {
        try {
          const parsed = JSON.parse(message.data) as WsEvent;
          handleEvent(parsed);
        } catch (error) {
          console.warn("ws: не парсится сообщение:", error, message.data);
        }
      };
      socket.onclose = () => {
        setStatus("disconnected");
        scheduleReconnect();
      };
      socket.onerror = () => {
        socket.close();
      };
    };

    const scheduleReconnect = (): void => {
      if (cancelledRef.current) return;
      const attempt = reconnectAttemptsRef.current++;
      const delay = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
      window.setTimeout(connect, delay);
    };

    void connect();

    return () => {
      cancelledRef.current = true;
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [queryClient, setStatus]);
}
