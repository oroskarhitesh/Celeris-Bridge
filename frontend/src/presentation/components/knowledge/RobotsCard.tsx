/**
 * What a site's own rules say, before blaming the crawler.
 *
 * This endpoint exists for one sentence: "the crawler indexed four pages" is
 * much easier to explain when the file disallowing the rest is on the screen.
 * So the text is printed verbatim rather than interpreted — the crawler obeys
 * the file, and a summary of it here could disagree with what the crawler
 * actually did.
 *
 * It is fetched on a click and never on render. Nothing about a form sitting
 * open is a reason to make a request to a stranger's server.
 *
 * No file is not an error. It means everything is allowed, which is the right
 * reading for a site the customer owns — so an absent robots.txt is reported
 * as a fact, in the words the crawler will act on.
 */

import { useState } from "react";

import { useLazyRobotsQuery } from "@/data/api/knowledgeApi";
import type { ApiError } from "@/data/client/errors";
import { Button } from "@/presentation/components/ui/Button";
import { Card } from "@/presentation/components/ui/Card";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { TextInput } from "@/presentation/components/ui/TextInput";

export function RobotsCard() {
  const [check, result] = useLazyRobotsQuery();
  const [url, setUrl] = useState("");

  return (
    <Card
      title="Check robots.txt"
      description="The crawler follows it. This is the same file, fetched the same way."
    >
      <div className="space-y-4">
        <TextInput
          id="robots-url"
          label="Any URL on the site"
          type="url"
          placeholder="https://example.com/docs"
          value={url}
          hint="The file is looked for at the site's root, whatever page is given."
          onChange={(event) => setUrl(event.target.value)}
        />

        {result.error ? <ErrorNotice error={result.error as ApiError} /> : null}

        {result.data ? (
          result.data.text === null ? (
            <p className="rounded-lg border border-line bg-canvas px-4 py-3 text-sm text-ink-soft">
              No robots.txt there, or it could not be read. Everything is
              allowed — which is the right reading for a site you own.
            </p>
          ) : (
            <pre className="max-h-64 overflow-auto rounded-lg border border-line bg-canvas px-4 py-3 text-xs text-ink">
              {result.data.text}
            </pre>
          )
        ) : null}

        <Button
          look="secondary"
          busy={result.isFetching}
          disabled={!url}
          onClick={() => check(url)}
        >
          Check
        </Button>
      </div>
    </Card>
  );
}
