import { useState } from "react";

import { DnsTab } from "./DnsTab";
import { GeoIpTab } from "./GeoIpTab";
import { IpsetGroupsTab } from "./IpsetGroupsTab";

interface Props {
  serverId: number;
  showSpark: boolean;
}

type ListsSubTab = "geoip" | "dns" | "ipset";

const SUB_TABS: Array<{ id: ListsSubTab; label: string }> = [
  { id: "geoip", label: "GeoIP" },
  { id: "dns", label: "DNS" },
  { id: "ipset", label: "Custom IPset" },
];

export function ListsTab({ serverId, showSpark }: Props) {
  const [active, setActive] = useState<ListsSubTab>("geoip");

  return (
    <>
      <div className="tab-switcher" style={{ marginBottom: 16 }}>
        {SUB_TABS.map((subTab) => (
          <button
            key={subTab.id}
            className={active === subTab.id ? "active" : ""}
            onClick={() => setActive(subTab.id)}
            type="button"
          >
            {subTab.label}
          </button>
        ))}
      </div>

      {active === "geoip" && <GeoIpTab serverId={serverId} showSpark={showSpark} />}
      {active === "dns" && <DnsTab serverId={serverId} showSpark={showSpark} />}
      {active === "ipset" && <IpsetGroupsTab serverId={serverId} showSpark={showSpark} />}
    </>
  );
}
