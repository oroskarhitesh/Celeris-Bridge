/**
 * What this workspace has given its agents.
 *
 * Knowledge is a workspace-level screen rather than something under one
 * agent, because that is what the API is: ``/v1/knowledge/*`` is scoped by
 * the credential's workspace, and a document is available to every agent in
 * it unless it was uploaded against particular ones.
 *
 * One query, and two tabs in the URL. The crawl tab is separate because it is
 * a different kind of act: uploading a file is instant and local, and crawling
 * is a long request against somebody else's server. Keeping them apart means
 * the list is not covered by a form nobody is using.
 *
 * The page owns the query; every panel below is given what it needs or owns
 * its own mutation, so each can be rendered in a test on its own.
 */

import { useSearchParams } from "react-router-dom";

import { useSourcesQuery } from "@/data/api/knowledgeApi";
import { CrawlForm } from "@/presentation/components/knowledge/CrawlForm";
import { ReindexCard } from "@/presentation/components/knowledge/ReindexCard";
import { RobotsCard } from "@/presentation/components/knowledge/RobotsCard";
import { SourceList } from "@/presentation/components/knowledge/SourceList";
import { UploadCard } from "@/presentation/components/knowledge/UploadCard";
import { EmptyState } from "@/presentation/components/ui/EmptyState";
import { Tabs } from "@/presentation/components/ui/Tabs";

const TABS = ["sources", "crawl"] as const;
type TabKey = (typeof TABS)[number];

const LABELS: Record<TabKey, string> = {
  sources: "Sources",
  crawl: "Crawl a site",
};

export function KnowledgePage() {
  const [params] = useSearchParams();
  const asked = params.get("tab");
  const tab: TabKey = TABS.find((one) => one === asked) ?? "sources";

  const { data, error, isLoading } = useSourcesQuery();

  const sources = data ?? [];
  const passages = sources.reduce((total, one) => total + one.passages, 0);
  // Worth its own number on the screen: a source with no text is finished,
  // not waiting, and it will never answer a question until somebody replaces
  // it. Buried in a list of forty it is invisible.
  const silent = sources.filter((one) => one.status !== "indexed").length;
  const nothing = !isLoading && !error && sources.length === 0;

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-ink">Knowledge</h1>
        <p className="text-sm text-ink-soft">
          {sources.length
            ? `${sources.length} ${sources.length === 1 ? "source" : "sources"} · ${passages} passages` +
              (silent ? ` · ${silent} answering nothing` : "")
            : "Documents and crawled pages your agents can answer from."}
        </p>
      </header>

      <Tabs
        current={tab}
        tabs={TABS.map((key) => ({
          key,
          label: LABELS[key],
          ...(key === "sources" && sources.length
            ? { note: String(sources.length) }
            : {}),
        }))}
      />

      {tab === "sources" ? (
        <div className="space-y-5">
          <UploadCard />
          {nothing ? (
            <EmptyState
              title="Nothing to answer from yet"
              description="Upload a document, or crawl a site. Until then the agent can only answer from its own instructions."
            />
          ) : (
            <SourceList sources={data} loading={isLoading} error={error} />
          )}
          {sources.length ? <ReindexCard /> : null}
        </div>
      ) : (
        <div className="space-y-5">
          <CrawlForm />
          <RobotsCard />
        </div>
      )}
    </div>
  );
}
