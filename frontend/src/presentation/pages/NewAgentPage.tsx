/**
 * Creating an agent: a type, a name, nothing else.
 *
 * No settings are offered here on purpose. Every setting has a default in the
 * agent type, so an agent is complete the moment it exists, and asking
 * someone to fill a form before they have seen the thing they are configuring
 * is how a product ends up with twelve required fields nobody understands.
 * Settings are the agent's own page, and they are a delta on those defaults.
 *
 * ``templateVersion`` is not sent either. Left out, the backend pins the
 * agent to the latest published version of the type at the moment of
 * creation, which is what "new" should mean.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useCreateAgentMutation } from "@/data/api/agentsApi";
import { useCatalogueQuery } from "@/data/api/catalogueApi";
import { problemsOf } from "@/data/client/errors";
import { CatalogueGrid } from "@/presentation/components/catalogue/CatalogueGrid";
import { Button } from "@/presentation/components/ui/Button";
import { Card } from "@/presentation/components/ui/Card";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { TextInput } from "@/presentation/components/ui/TextInput";
import { useToast } from "@/presentation/hooks/useToast";

export function NewAgentPage() {
  const { data: catalogue, error: catalogueError, isLoading } = useCatalogueQuery();
  const [create, creating] = useCreateAgentMutation();
  const navigate = useNavigate();
  const { say, complain } = useToast();

  const [templateKey, setTemplateKey] = useState("");
  const [label, setLabel] = useState("");

  const chosen = catalogue?.find((one) => one.templateKey === templateKey);
  const labelProblem = problemsOf(creating.error).find((one) =>
    one.path.includes("label"),
  );

  async function submit() {
    const result = await create({ templateKey, label: label.trim() });
    if ("error" in result) {
      complain(result.error);
      return;
    }
    say(result.data.label + " was created as a draft.");
    // Straight to the agent, because the next thing anybody wants is its
    // settings -- which is the page that arrives in the next chunk.
    navigate("/agents/" + result.data.id);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-ink">New agent</h1>
        <p className="text-sm text-ink-soft">
          Pick a type and give it a name. It starts as a draft, so nobody sees it until
          you publish.
        </p>
      </header>

      <Card title="Agent type">
        <CatalogueGrid
          entries={catalogue}
          loading={isLoading}
          error={catalogueError}
          selected={templateKey}
          onSelect={setTemplateKey}
        />
      </Card>

      <Card title="Name">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <TextInput
            label="What this agent is called"
            value={label}
            maxLength={120}
            onChange={(event) => setLabel(event.target.value)}
            hint="Only your workspace sees this. Visitors see the agent's own name, which is a setting."
            {...(labelProblem ? { problem: labelProblem.message } : {})}
          />
          {creating.error && !labelProblem ? (
            <ErrorNotice error={creating.error} />
          ) : null}
          <div className="flex items-center gap-3">
            <Button
              type="submit"
              look="primary"
              busy={creating.isLoading}
              disabled={!templateKey || !label.trim()}
            >
              Create draft
            </Button>
            {chosen ? (
              <span className="text-xs text-ink-soft">
                Pinned to {chosen.name} v{chosen.version}
              </span>
            ) : (
              <span className="text-xs text-ink-soft">Choose a type first</span>
            )}
          </div>
        </form>
      </Card>
    </div>
  );
}
