#!/usr/bin/env bash
# Smoke-drives the running Chunking Rules FastAPI backend (port 8000) and/or
# the Langflow instance (port 7860) that hosts the custom "Chunking Rules"
# component. Both servers must already be started (see SKILL.md).
#
# Usage:
#   .claude/skills/run-chunking-rules/driver.sh backend   # curl the chunk API
#   .claude/skills/run-chunking-rules/driver.sh langflow  # verify the component is registered
#   .claude/skills/run-chunking-rules/driver.sh both      # (default) run both checks
set -euo pipefail

MODE="${1:-both}"

check_backend() {
  echo "== backend: /api/strategies =="
  curl -sf http://localhost:8000/api/strategies | python3 -m json.tool | head -10

  echo
  echo "== backend: /api/chunk (fixed strategy, pasted text) =="
  curl -sf -X POST http://localhost:8000/api/chunk \
    -F "strategy=fixed" \
    -F "chunk_size=100" \
    -F "chunk_overlap=20" \
    -F "text=This is a smoke test of the chunking rules app. It should split this sample text into a couple of overlapping fixed-size chunks so we can confirm the pipeline works end to end." \
    | python3 -m json.tool
}

check_langflow() {
  echo "== langflow: health =="
  curl -sf -o /dev/null -w "HTTP %{http_code}\n" http://localhost:7860/health

  echo "== langflow: Chunking Rules component registered =="
  TOKEN=$(curl -sf http://localhost:7860/api/v1/auto_login | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
  curl -sf --compressed http://localhost:7860/api/v1/all -H "Authorization: Bearer $TOKEN" \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
hits = [k for k in data.get('chunking', {}) if 'ChunkingRules' in k]
if not hits:
    print('FAIL: Chunking Rules component not found under the chunking category', file=sys.stderr)
    sys.exit(1)
print('OK:', hits[0])
"
}

case "$MODE" in
  backend) check_backend ;;
  langflow) check_langflow ;;
  both) check_backend; echo; check_langflow ;;
  *) echo "usage: $0 [backend|langflow|both]" >&2; exit 1 ;;
esac
