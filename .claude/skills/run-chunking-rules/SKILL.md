---
name: run-chunking-rules
description: Start, build, and smoke-test the Chunking Rules app — the FastAPI backend/UI (port 8000) and the Langflow instance hosting the custom "Chunking Rules" component (port 7860). Use when asked to run, start, launch, build, or screenshot this app, or to verify the Langflow integration works.
---

Chunking Rules has two independently-launchable pieces sharing one Python venv:

- **Backend/UI** — FastAPI app (`backend/main.py`) serving both the REST API
  and the static frontend at `/`.
- **Langflow** — hosts `langflow_components/chunking/chunking_rules_component.py`
  as a custom node, so flows can call any of the ten chunking strategies.

Paths below are relative to the project root (`Chunking_Rules/`), not this
skill directory. The driver is
`.claude/skills/run-chunking-rules/driver.sh` — it curls both servers and is
the agent path; use it after starting the servers below.

## Prerequisites / build

Already done in this repo's `.venv` — nothing to install for a normal run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install langflow   # only needed for the Langflow half
```

## Run (agent path)

**1. Start the backend** (serves UI + API on :8000):

```bash
cd backend && nohup ../.venv/bin/uvicorn main:app --port 8000 > /tmp/chunking-app.log 2>&1 &
disown
```

Use `disown` (not just `&`) — a background job left attached to the shell
dies when that shell session ends; `nohup` alone is not enough in this
environment.

**2. Start Langflow** (serves the Chunking Rules node on :7860), from the
project root:

```bash
PYTHONPATH=backend nohup .venv/bin/langflow run --components-path langflow_components --port 7860 > /tmp/langflow.log 2>&1 &
disown
```

Langflow takes ~15-20s to finish "Application startup complete." — poll
`/health` rather than assuming it's ready immediately:

```bash
for i in $(seq 1 24); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:7860/health)
  [ "$code" = "200" ] && break
  sleep 5
done
```

**3. Drive both with the smoke-test script:**

```bash
.claude/skills/run-chunking-rules/driver.sh both       # backend + langflow
.claude/skills/run-chunking-rules/driver.sh backend    # just the chunk API
.claude/skills/run-chunking-rules/driver.sh langflow   # just the component check
```

`backend` mode POSTs real text to `/api/chunk` and prints the returned
chunks/stats — proves chunking actually runs, not just that the server is
up. `langflow` mode logs in via `/api/v1/auto_login`, fetches
`/api/v1/all`, and asserts the `chunking` category contains
`ChunkingRulesComponent`.

To stop either server: `lsof -ti:8000 | xargs kill` / `lsof -ti:7860 | xargs kill`.

## Run (human path)

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000. For Langflow, same command as above without
`nohup`/backgrounding, then open http://localhost:7860 and search "Chunking"
in the component sidebar to drag in the node.

## Gotchas

- **A backgrounded server dies silently when the launching shell session
  ends**, even with `nohup`. Symptom: `curl` gets "Connection refused" on a
  port that was working a few tool-calls ago. Fix: relaunch with `disown`
  after `&`, and re-verify with the driver before assuming a server is still
  up.
- **`/api/v1/all` on Langflow requires auth** — an unauthenticated `curl`
  returns `HTTP 403 {"detail":"No authentication credentials provided"}`.
  Naively iterating that JSON for a substring match (e.g. checking each
  top-level value for `"chunk"`) silently returns "not found" instead of
  erroring, because you're iterating the characters of the error string, not
  a real component list. Always check the HTTP status / log in via
  `/api/v1/auto_login` first (default dev instance has it enabled).
- **Langflow gzips `/api/v1/all`** — plain `curl` without `--compressed`
  returns binary gzip bytes that look like a hang or garbage output, not a
  clear error.
- **The component still "loads" even when nothing is listening for
  requests** — `aget_component_metadata` builds a fast lazy-loaded index
  (`display_name: "... (not fully loaded)"`) that appears even before full
  discovery; the reliable check is the authenticated `/api/v1/all`, which
  the driver script uses.
- Component file placement is load-bearing: it must sit one directory level
  under `--components-path` (`langflow_components/chunking/*.py`), not
  directly in `langflow_components/`. Already correct in this repo — see the
  docstring in `chunking_rules_component.py` if it ever moves.

## Troubleshooting

- `EADDRINUSE: address already in use 0.0.0.0:4000` on the *central-mac-api*
  companion server (unrelated project) — not this app; if you see it here
  it means a stray earlier instance is still bound to that port, not 8000
  or 7860.
- Backend `curl: (7) Failed to connect` on :8000 → server isn't running or
  died (see Gotchas); restart with the command above.
- Langflow slow to answer `/health` right after launch → normal, it's still
  running DB migrations / loading components; poll rather than fixed-sleep.
