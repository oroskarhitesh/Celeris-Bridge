# celeris_bridge frontend

Two layers, and the rule that keeps them apart:

* `src/data/` — the only code that knows a URL, a header or the error
  envelope. Nothing in it renders.
* `src/presentation/` — pages and components. Nothing in it fetches; it calls
  a hook from `src/data/api/`.

## Running it

```sh
cp .env.example .env     # VITE_API_URL points at the backend
npm install
npm run dev              # http://localhost:5173
```

The backend has to allow this origin, because the API is talked to
cross-origin on purpose — there is no dev proxy, so a missing allow-list
fails here instead of in production:

```sh
# in backend/
CB_ADMIN_ORIGINS=http://localhost:5173 CB_API_TOKENS=devtok:ws_dev \
  PYTHONPATH=src .venv/Scripts/python.exe -m uvicorn celeris_bridge.api.main:app
```

Then paste `devtok` into the token box on the overview page, or put it in
`VITE_API_TOKEN` for a development build.

## State

| What | Where it lives |
| --- | --- |
| Anything the server owns | the RTK Query cache, and nowhere else |
| The session token | `sessionSlice` (and `sessionStorage`, until real auth) |
| Toasts, interface state | `uiSlice` |

Server data is never copied into a slice: a copy is a second truth that goes
stale after the first mutation. Lists refresh through cache tags
(`src/data/client/tags.ts`), never a manual refetch.

## Checks

```sh
npm run typecheck
npm test
npm run build
```
