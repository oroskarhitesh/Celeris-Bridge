/**
 * The channels, as data.
 *
 * A separate module because two screens need the same two things from it: a
 * list to filter by, and a way to spell one for a person. The backend
 * validates the value against its own enum, so this list exists to be
 * *offered* -- and a value that is not in it is still shown as it arrived,
 * because a channel this build has not heard of is a fact about the data and
 * not a reason to print nothing.
 */

import type { Channel } from "@/data/types/conversation";

export const CHANNELS: Array<{ value: Channel; label: string }> = [
  { value: "web_chat", label: "Web chat" },
  { value: "email", label: "Email" },
  { value: "voice", label: "Voice" },
  { value: "whatsapp", label: "WhatsApp" },
];

export function channelLabel(value: string): string {
  return CHANNELS.find((one) => one.value === value)?.label ?? value;
}
