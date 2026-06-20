import type { Meta, StoryObj } from "@storybook/react";

import { DnsTab } from "../../pages/DnsTab";
import { GeoIpTab } from "../../pages/GeoIpTab";
import { IpsetGroupsTab } from "../../pages/IpsetGroupsTab";
import { ListsTab } from "../../pages/ListsTab";
import { SERVER_ID } from "../_mock/data";
import { withMockAuth, withSeededQuery } from "../_mock/withSeededQuery";

// Lists с под-табами (GeoIP/DNS/IPset). Плюс отдельные карточки на каждый под-экран.
const meta: Meta = {
  title: "Screens/Lists",
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => <div style={{ padding: 18, maxWidth: 1180 }}><Story /></div>,
    withSeededQuery(),
    withMockAuth,
  ],
};
export default meta;

type Story = StoryObj;

const args = { serverId: SERVER_ID, showSpark: true } as const;

export const Overview: Story = { render: () => <ListsTab {...args} /> };
export const GeoIp: Story = { render: () => <GeoIpTab {...args} /> };
export const Dns: Story = { render: () => <DnsTab {...args} /> };
export const IpsetGroups: Story = { render: () => <IpsetGroupsTab {...args} /> };
