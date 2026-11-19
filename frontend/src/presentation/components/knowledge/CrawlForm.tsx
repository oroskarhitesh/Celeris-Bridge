/**
 * Point the crawler at a site.
 *
 * **It says out loud that the crawl runs inside the request.** That is the
 * backend's known debt, not a detail to hide behind a spinner: forty pages at
 * half a second apart is longer than many proxies will wait, so somebody
 * choosing forty pages should know what they are choosing. The advice on the
 * screen is the real advice -- ask for fewer pages.
 *
 * **Every limit is optional and blank means "the configured one".** The API
 * does not publish its own caps, so this form does not invent numbers to show
 * as defaults; what it can promise is the direction. A request may only make
 * a crawl gentler: the server takes the lower of pages and depth and the
 * longer of the delays, so nothing typed here can raise a cap on how hard
 * this service hits somebody else's web server.
 */

import { useState } from "react";

import { useCrawlMutation } from "@/data/api/knowledgeApi";
import type { ApiError } from "@/data/client/errors";
import type { CrawlBody, CrawlReport } from "@/data/types/knowledge";
import { Button } from "@/presentation/components/ui/Button";
import { Card } from "@/presentation/components/ui/Card";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { TextInput } from "@/presentation/components/ui/TextInput";
import { useToast } from "@/presentation/hooks/useToast";

import { CrawlResult } from "./CrawlResult";

/** The bounds the API states for each field, so a 422 is not the way someone
 *  discovers them. The *effective* cap is the configured one, which is lower
 *  and which this form cannot see. */
const BOUNDS = {
  maxPages: { min: 1, max: 500 },
  maxDepth: { min: 0, max: 10 },
  delaySeconds: { min: 0, max: 30 },
} as const;

type Key = keyof typeof BOUNDS;

export function CrawlForm() {
  const [crawl, running] = useCrawlMutation();
  const { say } = useToast();

  const [startUrl, setStartUrl] = useState("");
  const [fields, setFields] = useState<Record<Key, string>>({
    maxPages: "",
    maxDepth: "",
    delaySeconds: "",
  });
  const [report, setReport] = useState<CrawlReport | null>(null);

  function problemWith(key: Key): string | undefined {
    const raw = fields[key];
    if (raw === "") return undefined;
    const value = Number(raw);
    const { min, max } = BOUNDS[key];
    if (Number.isNaN(value) || value < min || value > max) {
      return `Between ${min} and ${max}.`;
    }
    return undefined;
  }

  const problems = (Object.keys(BOUNDS) as Key[]).map(problemWith);
  const bad = problems.some(Boolean);

  async function run(): Promise<void> {
    if (!startUrl || bad) return;
    const body: CrawlBody = {
      startUrl,
      // Only what was actually typed. Sending a zero for an untouched field
      // would be asking for a crawl of no pages.
      ...(fields.maxPages ? { maxPages: Number(fields.maxPages) } : {}),
      ...(fields.maxDepth ? { maxDepth: Number(fields.maxDepth) } : {}),
      ...(fields.delaySeconds ? { delaySeconds: Number(fields.delaySeconds) } : {}),
    };
    const { data, error } = await crawl(body);
    if (error || !data) return;
    setReport(data);
    say(
      data.indexedPages
        ? `${data.indexedPages} pages indexed, ${data.passages} passages.`
        : "Nothing was indexed from that site.",
    );
  }

  const set = (key: Key) => (value: string) =>
    setFields((current) => ({ ...current, [key]: value }));

  return (
    <div className="space-y-5">
      <Card
        title="Crawl a site"
        description="Same-site pages only, following robots.txt. Each page becomes a source."
      >
        <div className="space-y-4">
          <TextInput
            id="crawl-url"
            label="Start URL"
            type="url"
            placeholder="https://example.com/docs"
            value={startUrl}
            onChange={(event) => setStartUrl(event.target.value)}
          />

          <div className="grid gap-4 sm:grid-cols-3">
            <TextInput
              id="crawl-max-pages"
              label="Pages, at most"
              type="number"
              min={BOUNDS.maxPages.min}
              max={BOUNDS.maxPages.max}
              value={fields.maxPages}
              placeholder="configured"
              problem={problemWith("maxPages")}
              onChange={(event) => set("maxPages")(event.target.value)}
            />
            <TextInput
              id="crawl-max-depth"
              label="Depth, at most"
              type="number"
              min={BOUNDS.maxDepth.min}
              max={BOUNDS.maxDepth.max}
              value={fields.maxDepth}
              placeholder="configured"
              problem={problemWith("maxDepth")}
              onChange={(event) => set("maxDepth")(event.target.value)}
            />
            <TextInput
              id="crawl-delay"
              label="Seconds between pages"
              type="number"
              step="0.1"
              min={BOUNDS.delaySeconds.min}
              max={BOUNDS.delaySeconds.max}
              value={fields.delaySeconds}
              placeholder="configured"
              problem={problemWith("delaySeconds")}
              onChange={(event) => set("delaySeconds")(event.target.value)}
            />
          </div>

          <p className="rounded-lg border border-warn/40 bg-warn/5 px-4 py-3 text-xs text-ink">
            A crawl runs inside this request — the page waits for it, and a
            proxy in front of the API may give up before a long one finishes.
            Ask for fewer pages, or a shorter delay, if it does. Pages already
            indexed and unchanged are not embedded again, so running it a
            second time is cheap.
          </p>

          {running.error ? <ErrorNotice error={running.error as ApiError} /> : null}

          <Button
            look="primary"
            busy={running.isLoading}
            disabled={!startUrl || bad}
            onClick={run}
          >
            {running.isLoading ? "Crawling…" : "Start crawl"}
          </Button>
        </div>
      </Card>

      {report ? <CrawlResult report={report} /> : null}
    </div>
  );
}
