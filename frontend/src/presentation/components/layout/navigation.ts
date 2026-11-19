/**
 * The side navigation, as data.
 *
 * Each later chunk adds one entry here and one route. Nothing else in the
 * shell changes, which is the point of keeping it out of the markup.
 */

export interface Destination {
  to: string;
  label: string;
}

export const DESTINATIONS: Destination[] = [
  { to: "/", label: "Overview" },
  { to: "/agents", label: "Agents" },
  { to: "/knowledge", label: "Knowledge" },
  { to: "/conversations", label: "Conversations" },
  { to: "/leads", label: "Leads" },
];
