# celeris_bridge — where it stands

Last updated: **2026-11-18**    

A from-scratch rebuild of the Marketing Agent in Python + FastAPI + React.
VoiceLink and rag-pipeline are read as references only; no code is copied from
either, and nothing here connects to rag-pipeline.

The one idea the whole thing rests on: **an agent type is data, not a class.**
A new agent type (Email Triage, next) is a JSON definition document plus any
abilities it genuinely needs — there is no `if agent_type == …` anywhere, and
there is not meant to be.

---

## Done

### Backend — complete and tested

81 source files, ~17,000 lines, plus 43 test files, ~15,400 lines.
**45 HTTP operations across 38 paths**, in 8 routers.

| Layer | What is there |
| --- | --- |
| `domain/` | The definition schema, `FieldSpec`, overrides and resolution, the four-level version hierarchy, qualification, the conversation engine |
| `services/` | Templates, instances, knowledge, conversations, resolution, leads |
| `ports/` + `adapters/` | Stores (in-memory + Mongo), chat (Anthropic), embeddings (Voyage/OpenAI/dev), documents, HTTP fetch, calendar — only the ports actually used |
| `composition/` | The one place that knows which adapter exists; `Application` built in the FastAPI lifespan |
| `api/` | Routers, the single error table, the principal/authenticator, two CORS policies |

Things worth remembering about the design:

- **Four levels.** `AgentTemplate` (mutable draft) → `AgentTemplateVersion`
  (immutable published definition) → `AgentInstance` (overrides + a *pinned*
  template version) → `AgentInstanceVersion` (immutable published snapshot).
  Live traffic resolves the pinned template version and the published
  snapshot — never the draft, never the template's latest.
- **Defaults in code, deltas in the database.** A reset is a deletion, not a
  write of the current default, so a setting that was reset follows a *better*
  default when a later version ships one.
- **One declaration, five uses.** A `FieldSpec` drives the default, the
  validator, the admin form control, the JSON Schema the model is given, and
  the prose rendering. The settings form is generated from it; a hand-built
  form would be the sixth copy and the one that disagrees.
- **Tenancy is structural.** The workspace comes from the credential, never
  from a body, query or header. Request bodies are `extra="forbid"`, so
  sending `workspaceId` is a 422 rather than an ignored field.
- **One error table, so no route contains a `try`.** Everything arrives as
  `{"error": {"code", "message", "details?}}` with a stable code. An unmapped
  exception is a 500 — the correct, loud answer for a bug.
- **Two trust boundaries, two CORS policies.** The widget answers any origin
  and *never* allows credentials, which is what makes the wildcard safe; the
  admin allow-list is explicit and empty by default.

Five built-in abilities: `search_knowledge`, `save_visitor_details`,
`find_slots`, `book_slot`, `suggest_replies`.

### Frontend — all six chunks

116 source files, ~8,600 lines, plus 22 test files, ~3,900 lines. 169 tests.

Vite + React 19 + TypeScript, Redux Toolkit + RTK Query, Tailwind 4.

```
src/data/            the only code that knows a URL, a header or the envelope
  client/            baseApi · errors · tags · credentials
  api/               health · catalogue · agents · agentSettings · agentVersions
                     · agentPreview · knowledge · conversations · leads
  types/             health · catalogue · agent · settings · knowledge
                     · conversation · lead
  settings/          changes — pure: what the draft changes, and the patch for it
  store/             store · hooks · slices/session · slices/ui · slices/draft
src/presentation/    pages and components; nothing here fetches
  pages/             Overview · Agents · NewAgent · Agent · Knowledge
                     · Conversations · Transcript · Leads · NotFound
  components/        ui ×14 · layout ×4 · session ×2 · system ×2 · agents ×5
                     · catalogue ×2 · agentDetail ×15 · knowledge ×7
                     · conversations ×8 · leads ×5
  hooks/             useToast · useDraftSave
  format.ts          dates and token counts, shared by the three new screens
src/widget/          the public chat. Imports nothing from src/data or
                     src/presentation, and is built on its own — see chunk 6
```

Rules held to, and worth keeping:

- **Server data lives only in the RTK Query cache.** Never copied into a
  slice; a copy is a second truth that goes stale after the first mutation.
- **Slices hold only what the server does not know**: the session token, UI
  state, and (coming in chunk 3) the dirty settings draft.
- **Invalidation by tag, never a manual refetch.** Mutations that return the
  fresh record write it into the cache, so the next write's `expectRevision`
  is never stale.
- **One file, one component.** A page composes; it never defines a second
  component inline.
- **No dev proxy**, on purpose: the API is talked to cross-origin in
  development too, so a missing `CB_ADMIN_ORIGINS` fails on a laptop instead
  of at deploy.

Working screens: the overview (backend reachability, credential state, token
entry), the agents list (status filter in the URL, pause, resume, two-click
delete), new-agent (catalogue tiles that go dead at the per-workspace limit,
then create → straight to the agent), the agent detail page (four tabs in the
URL: settings, abilities, versions, preview), knowledge (sources and crawl),
conversations with per-turn cost and a transcript screen, and the leads table.
Plus the widget, which is not a screen in this application at all.

### The settings form is generated, which is the point of chunk 3

No file in the frontend names a setting. `GET /v1/agents/{id}/settings` sends
each setting's `FieldSpec` — label, type, options, limits, default — plus the
resolved value and whether this workspace is the one that chose it, and the
form renders a control from that. A template version with a new setting grows
a control without a line changing here.

What that bought, and what it cost:

- **One switch on `spec.type`** draws string (one-line or multiline), enum,
  number, url, color and boolean; multiselect, stringList and `list` are their
  own components. A `list` row's fields are `ItemSpec`s, so rows reuse the
  same switch — a new setting type works inside rows for free.
- **The constraints go to the element**, not reimplemented: `maxLength` on the
  box, `min`/`max`/`step` on the number, `maxItems` disabling "Add". None of
  it is the real check — that is the server's, from the same declaration — but
  a control that cannot produce a rejected value beats explaining the refusal.
- **A locked setting is shown and not editable.** It is in the response so it
  can be read; patching one is `SETTING_LOCKED` and would take the whole
  request with it, so it never reaches a patch.
- **"Use default" is a local edit, not an endpoint.** Overrides are a delta, so
  writing today's default drops the override — and that setting then follows a
  *better* default when one ships. `/reset` is kept for the one thing a patch
  cannot say: drop everything, abilities included.
- **The unsaved draft is the one slice that holds server-shaped data.** Only
  touched entries, never a copy of the resolved values; `data/settings/changes`
  compares against the server's current answer, so typing a value and typing it
  back makes the save bar disappear on its own.
- **Publish refuses while anything is unsaved**, because it snapshots the saved
  draft — a publish from a screen with edits on it would ship something else.
  It is also dead when `hasUnpublishedChanges` is false, which is the same
  comparison that makes the server answer `NOTHING_TO_PUBLISH`.
- **The preview is two mutations and no cache.** A turn costs a model call and
  appends to a stored transcript, so nothing may repeat it — a refetch on tab
  focus would be a second billed message and a transcript growing by itself.
  A message that fails is taken back out of the transcript and put back in the
  box.

### Knowledge is chunk 4, and the interesting part is the three states

One screen at `/knowledge`, two tabs in the URL: sources and crawl. Uploading,
listing, downloading the original, deleting, reindexing, crawling and the
robots check — the whole of `/v1/knowledge/*` except scoping a document to
particular agents, which the API can do and this screen does not ask about.

- **A source is `indexed`, `empty` or `pending`, and `empty` is finished.** A
  document that failed to parse looks exactly like one nothing has happened to
  yet, which is why the backend distinguishes them; the badge says "No text"
  and the tooltip says what fixes it, because drawing that as "pending" sends
  somebody away to wait for a job that already ran. The header counts how many
  sources are answering nothing, since one of forty is invisible in a list.
- **The upload is the only request in the application that is not JSON.** It
  goes out as `FormData` with the content type left unset, because a multipart
  body is only parseable with the boundary the browser invents. The 20 MB cap
  is mirrored client-side — the server enforces it while reading, which is the
  check that counts, but refusing a 40 MB file after sending 20 MB of it is a
  refusal nobody should wait for.
- **The result is shown even when nothing was indexed.** `skipped` is the
  API's own sentence, written for whoever uploaded the file, and it is printed
  as it arrives. The alternative is a green tick over a document that will
  never answer a question.
- **A crawl report answers "why only four pages?"** — `stoppedEarly` first
  (budget, not links), then whether the site had rules at all, then the
  skipped list with the server's reason for each. `unchanged` pages are
  counted rather than hidden: on a second run most pages are unchanged, and a
  report that did not say so would look like a crawl that did nothing.
- **The form says out loud that the crawl runs inside the request.** That is
  the backend's known debt, and the advice on the screen is the real advice:
  ask for fewer pages. Every limit field is blank by default, because the
  configured caps are not published by the API and inventing numbers to show
  as defaults would be a lie; what the screen can promise is the direction —
  a request may only make a crawl gentler.
- **The robots check fetches nothing until it is asked to**, and prints the
  file verbatim. It reaches a stranger's server, so a form sitting open is not
  a reason to make that request; and the crawler obeys the file, so a summary
  here could disagree with what the crawler actually did.
- **The original downloads through the data layer, not through a link.** The
  endpoint needs the bearer token and an `<a href>` carries none, so the bytes
  arrive as a blob and the object URL is revoked immediately after the click.
- **Uploads and crawls invalidate the list and write nothing back by hand.**
  Unlike an agent write, these answer with a report rather than with the
  records they changed — forty crawled pages are forty sources — so
  reconstructing the list from the answer would be guessing what the server
  now holds.

### Two changes outside the chunk, both forced by it

**`messageOf` now quotes a 502 `FETCH_FAILED`.** The rule was that a 5xx never
shows the server's words, which is right for a 500: the backend sends none on
purpose. But `FETCH_FAILED` is the backend reporting that the *customer's*
site refused the crawler, and that sentence is the entire explanation for a
crawl that indexed nothing. Swallowing it left "something went wrong on the
server" pointing at a server that is working. The exception is a one-code set
with the reasoning next to it, so adding to it is a decision rather than a
habit.

**The test harness parses multipart by hand.** `fetchBaseQuery` hands `fetch`
a built `Request`, so the form is already serialised by the time the fake API
sees it — and `Request.formData()` cannot read it back here, because
`FormData` comes from undici and `File` comes from jsdom: undici writes the
body and then refuses to parse its own output, since the files in it are not
the `File` it recognises. Thirty lines of splitting on the boundary is the
price of not making the suite depend on that pairing. One consequence is
recorded in the test: the filename arrives as `blob` in this environment, so
the name a citation shows is sent as a field of its own rather than read off
the multipart filename — which is where that decision belongs anyway.

### Conversations and leads are chunk 5, and the shape of a lead is the design

Two screens over four read-only endpoints. `/conversations` lists what the
agents said with what each exchange cost, `/conversations/:id` is the
transcript, `/leads` is the table of what was collected. No writes anywhere,
because the admin API has none: a transcript is a record of model calls that
were billed, and an editable one could be made to disagree with them.

- **The leads table takes its columns from the data.** Which details an agent
  collects is a setting, so a table with "Name" and "Email" headings would be
  wrong for anybody who changed it — and showing the map as loose key/value
  pairs per row would throw away what a table is for: reading one column down
  forty rows to see who is missing a phone number. The columns are the union
  of the keys that actually arrived, in the order they were first seen, and a
  lead with nothing under one gets an em dash. That dash is the follow-up.
- **The verdict is history, not a calculation.** `status`, `missing` and
  `reasons` were recorded when the visitor said the thing that produced them,
  against the settings in force at the time, and the backend deliberately does
  not recompute them. So nothing on the screen offers to re-run the rules, and
  the panel on the transcript says when it was decided.
- **Leads leave out the passers-by by default, and say so.** The API defaults
  `withDetailsOnly` to false; the screen asks for true. A public widget has
  far more conversations than leads, and a row for everyone who said "hi"
  buries the ones worth calling. It is a checkbox in the URL and the header
  reads "including people who shared nothing" when it is on — a default rather
  than a hidden filter.
- **Previews are counted, not hidden.** A conversation with no instance
  version was had against a draft from the preview panel. The backend flags
  rather than hides them because a filtered-out conversation is one somebody
  goes looking for, so the row is labelled and the header says how many.
- **The transcript shows what a visitor never sees**, which is the whole
  reason the admin router is separate from the widget's: the model that
  answered, the tokens in and out, why the turn stopped, which abilities ran
  with their milliseconds and error codes, and the passages behind the answer
  with their scores. The passages are one click away, because a transcript is
  read as a conversation first and as a diagnosis second.
- **Tokens, never money.** This service holds no price list, so a currency
  symbol in front of a number nobody here can derive would be an invention
  dressed as an invoice.
- **A conversation carries an agent id and no label**, so both list screens
  load the agents list to turn ids into names — the same cache entry the
  agents screen uses — and fall back to the id when an agent has been deleted.
  A row with a blank where the name goes is worse than one that says the id.
- **Nothing is filtered client-side.** `limit` is applied by the server, so
  narrowing a list after it arrived would quietly hide rows that were inside
  the limit and leave the count on the screen lying.

### The widget is chunk 6, and it is a different application

`src/widget/` shares no code with the admin app and is built separately, to
`dist/widget/widget.js` at a stable filename. One `<script>` tag with
`data-workspace` and `data-instance` is the whole embed; the API host defaults
to wherever the script was served from.

- **It mounts into a shadow root with its own CSS, not Tailwind.** Tailwind's
  preflight resets margins, list styles and heading sizes on everything it
  loads beside, so shipping it inside a customer's page would mean a chat
  bubble that quietly restyles their site. The shadow root stops it in both
  directions, and `widget.html` is a deliberately hostile development page —
  serif body font, 62.5% root size, `content-box` on everything — so a leak in
  either direction is visible on a laptop.
- **No store, no router, no RTK Query.** The data layer is three fetches and a
  twenty-line envelope reader. It duplicates `data/client/errors` on purpose:
  importing it would pull in Redux and the habit of reaching for the rest.
  There is no cache and must not be — a reply is a billed model call appended
  to a stored transcript, so a cache that could replay a request is a cache
  that can say something twice.
- **Nothing is opened until somebody clicks.** The face is fetched on load
  because the launcher cannot be drawn without it, and it is also the origin
  check — a widget embedded where it does not belong draws nothing rather than
  failing on the visitor's first message. A *conversation* is a stored record,
  so starting one on page view would file a conversation for every visitor who
  never chats. Opening it makes no model call, so it can wait for the click.
- **A conversation is never resumed**, not across a reload and not from
  `sessionStorage`. The public API cannot read a transcript back — by design;
  that is the admin router's job — so a remembered id would restore a chat
  that looks empty to the visitor and is half over to the server.
- **A failed turn gives the message back, except when the server kept it.**
  `CONVERSATION_TOO_LONG` is a 409 and the backend stores the visitor's
  message anyway, so it stays on the screen and the backend's sentence is
  quoted — that one is written for a visitor to read. Every other failure
  removes the message and puts the words back in the box: it was not answered,
  and a transcript that claims a turn which did not happen is worse than an
  error. A 500's words are never shown to a visitor.
- **A refusal that will not change ends the conversation.** Paused agent,
  origin not allowed, no such agent: the box goes away rather than inviting
  somebody to keep trying on a page that is not going to work.
- **The launcher is the customer's.** Its text and its side of the screen are
  deployment settings, and a widget that puts itself where it likes is a
  widget that covers somebody's cookie banner.

### Three changes outside the chunks

**The preview mutations now invalidate the conversations list.** A preview is
stored like any other conversation and appears on that screen with its tokens
and its ability calls; without the tag, trying a draft and then opening the
list showed one that was missing the thing just done. It is the only tag in
`agentPreviewApi`, and a refetch of a list nobody is subscribed to costs
nothing.

**The three "worrying ending" sentences moved into one module.** `rounds`,
`empty` and `unavailable` are the same three facts in the draft preview and in
the stored transcript, and two copies of that prose would be two copies that
have to agree.

**Dates and token counts moved into `presentation/format.ts`.** The same
timestamp is now shown in a row, a turn and a lead; a transcript whose times
are formatted differently from the list it was opened from reads as two
systems.

### One backend defect found and fixed from the frontend, in chunk 4

`hasUnpublishedChanges` used to be `published_version is None or
draft_revision > 0` — and publishing bumps the draft revision, so it was true
the instant after a publish on an agent nobody had touched. It is now
`AgentInstance.differs_from(snapshot)`: a comparison of the pinned template
version and the overrides against the published snapshot, which is the same
check that makes publishing an unchanged draft a `NOTHING_TO_PUBLISH`. New
port method `published_many` answers it for a whole list in two queries rather
than two per row.

### And a second one, found the same way in chunk 6

`widgetEnabled` was stored, exposed on the agent view, settable through
`PUT /v1/agents/{id}/deployment` — and read by nothing. The public router
never looked at it, so switching the chat off changed nothing at all. It is
now checked in the widget router's one resolution helper, so all three public
endpoints answer the same way, and it is reported as `AGENT_NOT_LIVE` with a
reason rather than as a new code: from a visitor's side it is the same fact in
the same shape. An agent kept live for email or voice while its web chat is
off is an ordinary configuration, not an edge case. One new backend test.

---

## Remaining

### Admin screens with no chunk

All six planned frontend chunks are built. What is still missing on the admin
side is the **deployment form** — allowed origins, launcher position and text,
and the widget switch — which is now the most conspicuous gap: every one of
those settings is read by the widget and none of them can be changed except
with a `PUT`. Also unbuilt: the platform's own template editor, and anything
to do with users or billing.

The embed snippet is not shown anywhere either. A customer cannot currently
find out what to paste into their site without being told.

### Parity gaps against VoiceLink's marketing agent

| Gap | Where it stands |
| --- | --- |
| Real authentication, users, workspaces | Static bearer tokens stand in (`CB_API_TOKENS`); no sign-in, no sessions, no password reset |
| Google Calendar | Only `InMemoryCalendar`; `find_slots` and `book_slot` work against it |
| Round-robin employee selection | Not built (VoiceLink has `selectEmployeeRoundRobin` + `scheduling/roundRobin.js`) |
| `listProducts` ability | Not built |
| Background jobs / worker | A crawl runs inside the request |
| `riskTopics`, number-and-fact checking | Not built |
| Email Triage | Planned as one JSON definition, nothing else |

### Known debts

- Knowledge-gap recording (what visitors asked that nothing answered).
- Cross-turn idempotency, so a retried message cannot answer twice.
- Streaming replies; everything is a whole answer today.
- XML sitemaps are detected but not fetched.
- HTML is read with regexes rather than a parser.
- Composition opens two Mongo pools per process.
- No rate limiting anywhere — and the widget is now a public, unauthenticated
  surface that spends money per request.
- The widget bundles React, so it is 72 kB gzipped. Deliberate (a customer's
  page may have its own React, a different version, or none) but it is the
  one number in this project that a visitor pays for, and swapping React for
  Preact behind an alias would cut most of it.
- The leads table has no export and no "contacted" flag. Both would need a
  write the API does not have, and inventing one in the frontend would be a
  state that exists on one screen and nowhere else.
- **No real model or embedding call has ever been made from this machine** —
  there is no `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY` or `OPENAI_API_KEY` here,
  so every conversation test runs against a scripted model and the
  `hashing-dev:256` word-hash embedder.

---

## Running it

Backend:

```sh
cd backend
PYTHONPATH=src CB_ADAPTERS=memory CB_EMBEDDINGS=development \
  CB_API_TOKENS=devtok:ws_dev,plat::platform \
  CB_ADMIN_ORIGINS=http://localhost:5173 \
  .venv/Scripts/python.exe -m uvicorn celeris_bridge.api.main:app
```

`CB_ADAPTERS=mongo` with `CB_MONGO_URL` for the real store. The package is not
installed into the virtualenv, so `PYTHONPATH=src` is required.

Frontend:

```sh
cd frontend
cp .env.example .env     # VITE_API_URL points at the backend
npm install
npm run dev              # http://localhost:5173
```

The widget is served by the same dev server, from its own page, with the ids
on the query string:

```
http://localhost:5173/widget.html?workspace=ws_dev&instance=<agent id>&api=http://127.0.0.1:8000
```

That page is built to look like a customer's site rather than like this
project, which is the point of it. In production the widget is one tag:

```html
<script src="https://your-host/widget/widget.js"
        data-workspace="ws_live" data-instance="agent_7" defer></script>
```

An agent type has to exist before anything can be built on it: `POST
/v1/templates` with `backend/src/celeris_bridge/definitions/marketing_v1.json`
as the draft, then `POST /v1/templates/marketing_v1/publish`, both with the
platform token.

## Checks

| Where | Command | Last result |
| --- | --- | --- |
| Backend | `.venv/Scripts/python.exe -m pytest -q` | 1356 passed, 81 skipped (no Mongo) |
| Backend + Mongo | same, with `mongo:7` running | 1436 passed, 1 skipped |
| Frontend | `npm run typecheck && npm test` | clean, 169 passed |
| Frontend build | `npm run build` | app 477 kB / 146 kB gzipped |
| Widget build | same command, second pass | 228 kB / 72 kB gzipped |
