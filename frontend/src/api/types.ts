// Ручные типы под ответы control-plane. Можно автогенерить через
// `npm run generate-types` (нужен запущенный backend).

export type ServerStatus = "online" | "offline" | "degraded" | "provisioning" | "error";

export interface ServerSummary {
  id: number;
  host: string;
  port: number;
  name: string;
  version: string;
  status: ServerStatus;
  region: string | null;
  awg_containers: string[];
  added_at: string;
  last_seen_at: string | null;
}

export interface ServerListResponse {
  servers: ServerSummary[];
}

export interface RoutingRule {
  id: number;
  server_id: number;
  country: string;
  ipset_name: string;
  fwmark: number;
  table_id: number;
  via_interface: string;
  via_gateway: string;
  enabled: boolean;
}

export interface RoutingRuleListResponse {
  rules: RoutingRule[];
}

export interface DnsRule {
  id: number;
  server_id: number;
  name: string;
  domains: string[];
  ipset_name: string;
  enabled: boolean;
}

export interface DnsRuleListResponse {
  rules: DnsRule[];
}

export interface GeoList {
  id: number;
  country: string;
  name: string;
  source_url: string;
  ipv4_count: number;
  ipv6_count: number;
  custom_count: number;
  last_synced_at: string | null;
  status: "synced" | "stale" | "error";
}

export interface GeoListListResponse {
  lists: GeoList[];
}

export type MetricsRange = "1h" | "6h" | "24h";

export interface MetricsPoint {
  timestamp: string;
  rx_bytes: number;
  tx_bytes: number;
}

export interface MetricsRangeResponse {
  range: MetricsRange;
  points: MetricsPoint[];
}

export interface ApplyRulesResponse {
  applied: number;
  skipped: number;
  errors: string[];
}

export interface ApplyDnsResponse {
  applied: number;
  errors: string[];
}

export type TunnelStatus = "up" | "down" | "degraded";

export interface PeerInfo {
  public_key: string;
  endpoint: string | null;
  last_handshake: string | null;
  rx_bytes: number;
  tx_bytes: number;
}

export interface TunnelInfo {
  container_name: string;
  interface: string;
  peers: PeerInfo[];
  status: TunnelStatus;
}

export interface TunnelsResponse {
  tunnels: TunnelInfo[];
}

export interface GeoIpSyncResponse {
  cidrs_loaded: number;
  ipset_name: string;
  duration_ms: number;
}

export type TlsMode = "upload" | "path" | "acme";
export type AcmeChallenge = "http01" | "dns01";
export type DnsProvider = "cloudflare" | "desec" | "route53";

export interface TlsConfigPayload {
  mode: TlsMode;
  port: number;
  cert_pem?: string | null;
  key_pem?: string | null;
  cert_path?: string | null;
  key_path?: string | null;
  domains?: string[];
  email?: string | null;
  challenge?: AcmeChallenge | null;
  dns_provider?: DnsProvider | null;
  dns_api_key?: string | null;
}

export interface TlsApplyResponse {
  cert_path: string;
  expires_at: string;
  domains: string[];
}

export interface TlsConfigResponse {
  server_id: number;
  config: Record<string, unknown>;
  expires_at: string | null;
}

export interface ProvisionPayload {
  host: string;
  ssh_port?: number;
  ssh_user?: string;
  ssh_password?: string | null;
  ssh_private_key?: string | null;
  name: string;
  region?: string | null;
  agent_port?: number | null;
}

export interface UpdateServerPayload {
  version: string;
  wheel_url: string;
  wait_for_reconnect?: boolean;
}

export interface UpdateResponse {
  previous_version: string;
  status: "restarting";
}

export type WsEventType =
  | "server.status_changed"
  | "server.metrics"
  | "server.agent_updated"
  | "rule.applied"
  | "dns.applied"
  | "geoip.synced"
  | "tls.applied"
  | "provision.progress";

export interface WsEvent {
  type: WsEventType;
  payload: Record<string, unknown>;
  server_id: number | null;
  timestamp: string;
}
