/**
 * The rules the agent is held to, whatever its settings say.
 *
 * Shown and never editable. A workspace cannot turn these off -- they are the
 * platform's, and they are the reason a settings screen can be as open as it
 * is -- but it should be able to read them: "never claim to be a person" is
 * something a customer needs to know their agent does, not a secret.
 */

import type { LockedRule } from "@/data/types/settings";
import { Card } from "@/presentation/components/ui/Card";

export function LockedRules({ rules }: { rules: LockedRule[] }) {
  if (!rules.length) return null;
  return (
    <Card
      title="Rules that cannot be changed"
      description="Enforced on every reply, in every channel, whatever the settings above say."
    >
      <ul className="space-y-2">
        {rules.map((rule) => (
          <li key={rule.id} className="flex gap-2 text-sm text-ink-soft">
            <span aria-hidden="true" className="text-ink-soft">
              ·
            </span>
            {rule.text}
          </li>
        ))}
      </ul>
    </Card>
  );
}
