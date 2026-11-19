/**
 * One agent: everything about it, in four tabs.
 *
 * It composes and does not draw controls. Two queries belong to it -- the
 * agent and its generated settings form -- and every panel is given what it
 * needs, so a panel can be rendered in a test with a fixed object.
 *
 * **The agent comes from ``useAgentQuery``, not from ``form.agent``.** The
 * settings form carries a copy, and two copies of a record with a revision
 * number on it is the bug where a button sends a stale ``expectRevision``
 * from whichever one rendered last. Every mutation writes the fresh agent
 * into this cache entry, so there is one number on the screen and it is the
 * current one.
 *
 * **The tab is in the URL** so a link to someone's versions tab opens the
 * versions tab, and the back button goes back a tab rather than off the page.
 *
 * **The draft is opened by id on every render.** That dispatch is what drops
 * another agent's unsaved edits when someone navigates between two agents,
 * and it is a no-op for the same id -- so switching tabs keeps what was
 * typed.
 */

import { useEffect } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import { useAgentQuery } from "@/data/api/agentsApi";
import { useSettingsFormQuery } from "@/data/api/agentSettingsApi";
import { useAgentVersionsQuery } from "@/data/api/agentVersionsApi";
import { changeCount } from "@/data/settings/changes";
import { useAppDispatch, useAppSelector } from "@/data/store/hooks";
import { draftOpened, selectDraft } from "@/data/store/slices/draftSlice";
import { AgentActions } from "@/presentation/components/agents/AgentActions";
import { AgentStatusBadge } from "@/presentation/components/agents/AgentStatusBadge";
import { AbilitiesPanel } from "@/presentation/components/agentDetail/AbilitiesPanel";
import { PreviewChat } from "@/presentation/components/agentDetail/PreviewChat";
import { PublishPanel } from "@/presentation/components/agentDetail/PublishPanel";
import { SettingsPanel } from "@/presentation/components/agentDetail/SettingsPanel";
import { UpgradePanel } from "@/presentation/components/agentDetail/UpgradePanel";
import { VersionHistory } from "@/presentation/components/agentDetail/VersionHistory";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";
import { Tabs } from "@/presentation/components/ui/Tabs";

const TABS = ["settings", "abilities", "versions", "preview"] as const;
type TabKey = (typeof TABS)[number];

const LABELS: Record<TabKey, string> = {
  settings: "Settings",
  abilities: "Abilities",
  versions: "Versions",
  preview: "Preview",
};

export function AgentPage() {
  const { id = "" } = useParams();
  const [params] = useSearchParams();
  const dispatch = useAppDispatch();
  const draft = useAppSelector(selectDraft);

  const agent = useAgentQuery(id, { skip: !id });
  const form = useSettingsFormQuery(id, { skip: !id });

  // An effect rather than a render-time dispatch: this is a store write, and
  // a write during render is the one thing React will not forgive.
  useEffect(() => {
    if (id) dispatch(draftOpened(id));
  }, [dispatch, id]);

  const asked = params.get("tab");
  const tab: TabKey = TABS.find((one) => one === asked) ?? "settings";

  // Only where it is shown. Nothing else on the page needs the history, and
  // a read per agent opened would be a read nobody asked for.
  const versions = useAgentVersionsQuery(id, { skip: !id || tab !== "versions" });

  if (agent.isLoading || form.isLoading) return <Spinner label="Loading agent" />;
  if (agent.error) return <ErrorNotice error={agent.error} />;
  if (!agent.data) return <ErrorNotice error={form.error} />;

  const record = agent.data;
  const unsaved = form.data ? changeCount(form.data, draft) : 0;

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="truncate text-lg font-semibold text-ink">{record.label}</h1>
            <AgentStatusBadge agent={record} />
          </div>
          <p className="mt-1 text-sm text-ink-soft">
            {form.data?.name ?? record.templateKey} · pinned to v
            {record.draftTemplateVersion}
          </p>
        </div>
        <AgentActions agent={record} />
      </header>

      <Tabs
        current={tab}
        tabs={TABS.map((key) => ({
          key,
          label: LABELS[key],
          ...(key === "settings" && unsaved ? { note: String(unsaved) } : {}),
        }))}
      />

      {form.error && !form.data ? <ErrorNotice error={form.error} /> : null}

      {tab === "settings" && form.data ? (
        <SettingsPanel agent={record} form={form.data} />
      ) : null}

      {tab === "abilities" && form.data ? (
        <AbilitiesPanel agent={record} form={form.data} />
      ) : null}

      {tab === "versions" ? (
        <div className="space-y-5">
          <PublishPanel agent={record} unsaved={unsaved} />
          <UpgradePanel agent={record} />
          <VersionHistory
            agent={record}
            versions={versions.data}
            loading={versions.isLoading}
            error={versions.error}
          />
        </div>
      ) : null}

      {tab === "preview" ? <PreviewChat agent={record} /> : null}
    </div>
  );
}
