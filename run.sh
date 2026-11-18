#!/usr/bin/env bash
# Start the brain backend (ws://127.0.0.1:8765/ws) and the webapp dev server (http://localhost:5173).
#   ./run.sh                  # real backend, target app Dofus, mode observe
#   ./run.sh --mock           # fixtures + mock LLM, no game needed
#   ./run.sh --app TextEdit   # any extra args go to python -m dofus_brain.server
#   BRAIN_PORT=8766 ./run.sh  # if 8765 is taken (the page is then opened with ?ws=...)
# Ctrl+C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# The passive packet listener may require macOS administrator approval, but
# Dofus, Frida and the local Zaap service must run in the logged-in desktop
# session.  Root does not fix Frida's separate macOS task-port authorization
# requirement, and a root-run dashboard leaves credentials/captures owned by
# root.  Keep the application user-owned; the capture path escalates only the
# one tcpdump operation when it is explicitly started.
# Re-exec as the invoking user before starting either the backend or Vite.
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    echo "run.sh: dashboard/game services run as $SUDO_USER (not root); macOS will request capture permission only if needed."
    exec /usr/bin/sudo -H -u "$SUDO_USER" "$0" "$@"
  fi
  echo "Do not run this app as root. Run ./run.sh from your logged-in macOS account." >&2
  exit 1
fi

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
else
  echo "no .venv: python3 -m venv .venv && source .venv/bin/activate && pip install -e 'brain[mac,dev]' -e control" >&2
  exit 1
fi
[[ -d webapp/node_modules ]] || (cd webapp && npm install)

# Homebrew node may lose a dylib after a dependency upgrade (e.g. libllhttp.9.3). Then put
# a compatible lib under .tools/libshim/ and run vite with node directly: npm's
# `#!/usr/bin/env node` goes through SIP-protected /usr/bin/env, which drops DYLD_* vars.
WEB_PORT="${WEB_PORT:-5173}"
PORT="${BRAIN_PORT:-8765}"

# `run.sh` is the one owner of the local app.  A previous Ctrl+C, a crashed
# Vite process, or an earlier sudo launch must not leave one half of the app
# holding its port and cause the newly-started backend to be torn down.
stop_listener() {
  local port="$1" pids pid
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "$pids" ]] && return 0
  echo "stopping previous listener on port $port: $pids"
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
  for _ in 1 2 3 4 5; do
    sleep 0.2
    lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 || return 0
  done
  echo "port $port is still in use; stop that process and run ./run.sh again" >&2
  exit 1
}

stop_listener "$PORT"
stop_listener "$WEB_PORT"

# Keep the dev-server's local asset proxy on the same backend as the WebSocket.
# This matters when BRAIN_PORT is overridden: item icons must not silently point
# at an old server on the default port.
export VITE_API_PORT="$PORT"
VITE=(npm run dev -- --port "$WEB_PORT")
if [[ -d .tools/libshim ]]; then
  export DYLD_FALLBACK_LIBRARY_PATH="$ROOT/.tools/libshim"
  VITE=(node node_modules/vite/bin/vite.js --port "$WEB_PORT")
fi

pids=()
cleanup() {
  trap - INT TERM EXIT
  for p in ${pids[@]+"${pids[@]}"}; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# The local model service is a normal-user process, exactly like Dofus.  The
# bundled binary is intentionally not assumed to be on PATH.  Start it only
# when the configured loopback endpoint is absent; Ollama loads qwen3.5:4b on
# demand, so this itself does not consume the model's ~5 GB.
OLLAMA_BIN="$ROOT/.tools/ollama/ollama"
if [[ " $* " != *" --mock "* ]] && [[ -x "$OLLAMA_BIN" ]] && ! curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
  "$OLLAMA_BIN" serve &
  pids+=($!)
  echo "started local Ollama service"
fi

WS="ws://127.0.0.1:$PORT/ws"
URL="http://localhost:$WEB_PORT"
[[ "$PORT" == 8765 ]] || URL="$URL/?ws=$WS"

python -m dofus_brain.server --port "$PORT" "$@" &
pids+=($!)
(cd webapp && exec "${VITE[@]}") &
pids+=($!)

echo "backend  $WS"
echo "webapp   $URL"
# bash 3.2 (macOS) has no `wait -n`: stop both as soon as either exits
while kill -0 "${pids[0]}" 2>/dev/null && kill -0 "${pids[1]}" 2>/dev/null; do sleep 1; done
