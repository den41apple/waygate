import type { TabId } from "../store/ui";
import { Icon, type IconName } from "./Icon";

export interface TabItem {
  id: TabId;
  label: string;
  icon: IconName;
  count?: number | null;
}

interface Props {
  tab: TabId;
  onTab: (tab: TabId) => void;
  tabs: TabItem[];
}

export function Tabs({ tab, onTab, tabs }: Props) {
  return (
    <div className="tabs-wrap">
      {tabs.map((item) => (
        <button
          key={item.id}
          className={`tab ${tab === item.id ? "active" : ""}`}
          onClick={() => onTab(item.id)}
        >
          <Icon name={item.icon} size={14} />
          {item.label}
          {item.count != null && <span className="count">{item.count}</span>}
        </button>
      ))}
    </div>
  );
}
