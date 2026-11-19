/**
 * Give the agent a document.
 *
 * Three things here are deliberate.
 *
 * **The size is checked before anything is sent.** The API enforces the same
 * cap while reading the body, which is the check that counts -- but refusing
 * a 40 MB file after uploading 20 MB of it is a refusal nobody should have to
 * wait for. Both exist, and the server's is the real one.
 *
 * **An id may be given, and that is how a document is replaced.** Upload with
 * the id of something already there and it takes its place, passages and all.
 * Left blank, the server invents one -- which is the right default, because an
 * id a client chooses is an id a client can collide with.
 *
 * **The result is shown, including when nothing was indexed.** A file can be
 * accepted, stored, and contain no text; the API says so in ``skipped`` and
 * this prints it. The alternative is a green tick over a document that will
 * never answer a question.
 */

import { useRef, useState } from "react";

import {
  MAX_UPLOAD_BYTES,
  SOURCE_ID_PATTERN,
  useUploadSourceMutation,
} from "@/data/api/knowledgeApi";
import type { ApiError } from "@/data/client/errors";
import type { Ingested } from "@/data/types/knowledge";
import { Button } from "@/presentation/components/ui/Button";
import { Card } from "@/presentation/components/ui/Card";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { TextInput } from "@/presentation/components/ui/TextInput";
import { Toggle } from "@/presentation/components/ui/Toggle";
import { useToast } from "@/presentation/hooks/useToast";

const MEGABYTES = MAX_UPLOAD_BYTES / (1024 * 1024);

export function UploadCard() {
  const [upload, uploading] = useUploadSourceMutation();
  const { say } = useToast();

  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [approved, setApproved] = useState(false);
  const [result, setResult] = useState<Ingested | null>(null);

  const tooBig = file !== null && file.size > MAX_UPLOAD_BYTES;
  const badId = sourceId !== "" && !SOURCE_ID_PATTERN.test(sourceId);

  function clear(): void {
    setFile(null);
    setName("");
    setSourceId("");
    setApproved(false);
    // The file input holds its own value, so clearing state is not enough:
    // without this the chosen file name stays on the screen after the upload.
    if (input.current) input.current.value = "";
  }

  async function send(): Promise<void> {
    if (!file || tooBig || badId) return;
    const { data, error } = await upload({
      file,
      // The file's own name when nothing was typed, rather than leaving the
      // field empty and letting the server fall back to the multipart
      // filename. It would -- but the name is what a citation shows, this
      // screen is where that is decided, and the placeholder says so.
      name: name || file.name,
      ...(sourceId ? { sourceId } : {}),
      approved,
    });
    // A refusal is drawn inline, under the controls that produced it, and the
    // form keeps what was typed so it can be sent again.
    if (error || !data) return;
    setResult(data);
    clear();
    say(
      data.indexed
        ? `${data.sourceName}: ${data.passages} passages indexed.`
        : `${data.sourceName} was stored, but nothing was indexed.`,
    );
  }

  return (
    <Card
      title="Add a document"
      description={`PDF, Word, HTML, Markdown or plain text, up to ${MEGABYTES} MB.`}
    >
      <div className="space-y-4">
        <div className="space-y-1">
          <label htmlFor="upload-file" className="block text-sm font-medium text-ink">
            File
          </label>
          <input
            ref={input}
            id="upload-file"
            type="file"
            aria-invalid={tooBig ? true : undefined}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setResult(null);
            }}
            className="block w-full cursor-pointer rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink file:mr-3 file:rounded file:border-0 file:bg-canvas file:px-2 file:py-1 file:text-sm file:text-ink"
          />
          {tooBig && file ? (
            <p role="alert" className="text-xs text-bad">
              {`That file is ${(file.size / (1024 * 1024)).toFixed(1)} MB. The limit is ${MEGABYTES} MB.`}
            </p>
          ) : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <TextInput
            id="upload-name"
            label="Name"
            value={name}
            placeholder={file ? file.name : "The file's own name"}
            hint="What a citation will show."
            onChange={(event) => setName(event.target.value)}
          />
          <TextInput
            id="upload-source-id"
            label="Replace (source id)"
            value={sourceId}
            placeholder="Leave empty for a new document"
            problem={badId ? "Letters, numbers, dashes and underscores only." : undefined}
            hint="Uploading with an existing id replaces that document."
            onChange={(event) => setSourceId(event.target.value)}
          />
        </div>

        <Toggle
          id="upload-approved"
          label="An approved answer — rank it above ordinary passages"
          checked={approved}
          onChange={setApproved}
        />

        {uploading.error ? <ErrorNotice error={uploading.error as ApiError} /> : null}

        {result ? (
          <div className="rounded-lg border border-line bg-canvas px-4 py-3 text-sm">
            <p className="font-medium text-ink">{result.sourceName}</p>
            {result.indexed ? (
              <p className="mt-0.5 text-ink-soft">
                {`${result.passages} passages from ${result.characters.toLocaleString()} characters, embedded with ${result.model}.`}
              </p>
            ) : (
              <p className="mt-0.5 text-warn">
                {/* The API's own sentence. It is written for whoever uploaded
                    the file, because they are the only person who can act on
                    it. */}
                {result.skipped || "Nothing was indexed from it."}
              </p>
            )}
            <p className="mt-1 text-xs text-ink-soft">
              Source id <code>{result.sourceId}</code>
            </p>
          </div>
        ) : null}

        <div className="flex items-center gap-3">
          <Button
            look="primary"
            busy={uploading.isLoading}
            disabled={!file || tooBig || badId}
            onClick={send}
          >
            Upload
          </Button>
          {file ? (
            <Button look="ghost" onClick={clear}>
              Clear
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}
