/**
 * What a crawl actually did.
 *
 * The report exists to answer one question — "why four pages?" — so three
 * fields are given more room than their size suggests.
 *
 * ``stoppedEarly`` is the difference between "that is the whole site" and
 * "raise the cap", and it is the first thing shown when it is set.
 *
 * ``robotsPresent`` is why a crawl of a large site can index almost nothing
 * and still be working correctly. The site's own rules are the usual
 * explanation, and an admin who cannot see that will file a bug instead.
 *
 * ``unchanged`` on a page means it was fetched and matched what is already
 * indexed, so nothing was embedded again. On the second run most pages are
 * unchanged; a list that did not say so would look like a crawl that did
 * nothing.
 */

import type { CrawlReport } from "@/data/types/knowledge";
import { Card } from "@/presentation/components/ui/Card";

export function CrawlResult({ report }: { report: CrawlReport }) {
  const unchanged = report.pages.filter((one) => one.unchanged).length;

  return (
    <Card title="Crawl report" description={report.startUrl}>
      <div className="space-y-4">
        <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
          <div>
            <dt className="text-xs text-ink-soft">Indexed</dt>
            <dd className="font-medium text-ink">{report.indexedPages} pages</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-soft">Passages</dt>
            <dd className="font-medium text-ink">{report.passages}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-soft">Unchanged</dt>
            <dd className="font-medium text-ink">{unchanged}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-soft">Skipped</dt>
            <dd className="font-medium text-ink">{report.skipped.length}</dd>
          </div>
          <div>
            <dt className="text-xs text-ink-soft">robots.txt</dt>
            <dd className="font-medium text-ink">
              {report.robotsPresent ? "followed" : "none found"}
            </dd>
          </div>
        </dl>

        {report.stoppedEarly ? (
          <p
            role="status"
            className="rounded-lg border border-warn/40 bg-warn/5 px-4 py-3 text-sm text-ink"
          >
            <span className="font-medium">Stopped early:</span> {report.stoppedEarly}
          </p>
        ) : null}

        {report.pages.length ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
              Pages
            </h3>
            <ul className="mt-2 divide-y divide-line">
              {report.pages.map((page) => (
                <li
                  key={page.url}
                  className="flex items-start justify-between gap-4 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm text-ink">
                      {page.title || page.url}
                    </p>
                    <p className="truncate text-xs text-ink-soft">{page.url}</p>
                  </div>
                  <p className="shrink-0 text-xs text-ink-soft">
                    {`depth ${page.depth} · `}
                    {page.unchanged ? "unchanged" : `${page.passages} passages`}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {report.skipped.length ? (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
              Skipped
            </h3>
            {/* Each with the server's own reason. "Disallowed by robots.txt"
                and "not the same site" are different problems with different
                answers, and only one of them is worth acting on. */}
            <ul className="mt-2 space-y-1">
              {report.skipped.map((one) => (
                <li key={one.url} className="text-xs text-ink-soft">
                  <span className="text-ink">{one.url}</span> — {one.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
