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
  ssh_user: string;
  ssh_port: number;
  has_ssh_password: boolean;
  has_ssh_private_key: boolean;
}

export interface ServerListResponse {
  servers: ServerSummary[];
}

export type RoutingScope = "host" | "container";

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
  scope: RoutingScope;
  scope_target: string | null;
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
  /** Сохранить ssh-креды для server-side update'ов (Fernet поверх SECRET_KEY). */
  save_ssh_credentials?: boolean;
}

export interface UpdateServerPayload {
  version: string;
  wheel_url: string;
  wait_for_reconnect?: boolean;
}

export interface UpdateStartResponse {
  server_id: number;
  target_version: string;
  stream_url: string;
}

export type AwgClientStatus = "pending" | "running" | "stopped" | "error";

export interface ContainerInfo {
  name: string;
  status: AwgClientStatus;
  image: string;
  is_waygate_managed: boolean;
}

export interface ContainerListResponse {
  containers: ContainerInfo[];
}

export interface AwgClient {
  id: number;
  server_id: number;
  name: string;
  container_name: string;
  status: AwgClientStatus;
  country: string | null;
  peer_endpoint: string | null;
  peer_pubkey: string | null;
  interface_address: string | null;
  created_at: string;
  updated_at: string;
}

export interface AwgClientListResponse {
  clients: AwgClient[];
}

export interface AwgClientCreate {
  name: string;
  country?: string | null;
  config_text: string;
}

export interface IpsetGroup {
  id: number;
  server_id: number;
  name: string;
  cidrs: string[];
  created_at: string;
  updated_at: string;
}

export interface IpsetGroupListResponse {
  groups: IpsetGroup[];
}

export interface IpsetGroupCreate {
  name: string;
  cidrs: string[];
}

export interface Direction {
  id: number;
  server_id: number;
  awg_client_id: number | null;
  name: string;
  fwmark: number;
  table_id: number;
  via_interface: string;
  via_gateway: string;
  scope: "host" | "container";
  scope_target: string | null;
  enabled: boolean;
  geo_list_ids: number[];
  dns_rule_ids: number[];
  ipset_group_ids: number[];
  created_at: string;
  updated_at: string;
}

export interface DirectionListResponse {
  directions: Direction[];
}

export interface DirectionCreate {
  name: string;
  awg_client_id?: number | null;
  via_interface: string;
  via_gateway: string;
  geo_list_ids?: number[];
  dns_rule_ids?: number[];
  ipset_group_ids?: number[];
  scope?: "host" | "container";
  scope_target?: string | null;
  enabled?: boolean;
}

export interface DirectionUpdate {
  name?: string;
  awg_client_id?: number | null;
  via_interface?: string;
  via_gateway?: string;
  geo_list_ids?: number[];
  dns_rule_ids?: number[];
  ipset_group_ids?: number[];
  scope?: "host" | "container";
  scope_target?: string | null;
  enabled?: boolean;
}

export type WsEventType =
  | "server.created"
  | "server.deleted"
  | "server.status_changed"
  | "server.metrics"
  | "server.agent_updated"
  | "rule.applied"
  | "dns.applied"
  | "geoip.synced"
  | "tls.applied"
  | "provision.progress"
  | "awg_client.created"
  | "awg_client.deleted"
  | "awg_client.status_changed"
  | "direction.created"
  | "direction.updated"
  | "direction.deleted";

export interface WsEvent {
  type: WsEventType;
  payload: Record<string, unknown>;
  server_id: number | null;
  timestamp: string;
}

export interface AgentRelease {
  tag: string;
  version: string;
  name: string;
  published_at: string;
  wheel_url: string;
}

export interface AgentReleasesResponse {
  releases: AgentRelease[];
}
