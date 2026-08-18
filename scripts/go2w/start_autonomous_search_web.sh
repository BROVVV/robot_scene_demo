#!/usr/bin/env bash
set -euo pipefail

# Go2-W Autonomous Semantic Search WebUI launcher (plan book §76-§79).
#
#   bash scripts/go2w/start_autonomous_search_web.sh
#       # read-only start: camera + LLM + Manual WASD, search dry-run only
#   bash scripts/go2w/start_autonomous_search_web.sh --enable-autonomous-motion
#       # authorize the autonomous search to drive the robot (<=30 deg turns,
#       # <=0.30 m steps through the existing /go2w/motion safety stack)
#   bash scripts/go2w/start_autonomous_search_web.sh --mock
#       # offline frontend dev: mock backend, no robot / ROS required
#
# This script never starts Nav2 / Point-LIO / the Pandar driver itself; it
# only launches the single FastAPI server (manual + autonomous console) and
# only stops processes the project itself owns.

ENABLE_MOTION=0
MOCK=0
for arg in "$@"; do
  case "$arg" in
    --enable-autonomous-motion) ENABLE_MOTION=1 ;;
    --mock) MOCK=1 ;;
    *) printf 'ERROR: unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
runtime_root="${project_root}/outputs/autonomous_search/runtime"
log_root="${project_root}/outputs/autonomous_search/logs"
mkdir -p "${runtime_root}" "${log_root}"

host="${AUTONOMOUS_SEARCH_HOST:-127.0.0.1}"
port="${AUTONOMOUS_SEARCH_PORT:-8765}"

# ---- 0. Port conflict: only stop project-owned Go2-W web servers ---------- #
if curl -fsS "http://${host}:${port}/api/status" >/dev/null 2>&1; then
  printf 'Port %s already serves a Go2-W web demo.\n' "${port}" >&2
  for pidfile in \
    "${project_root}/outputs/manual_web_demo/runtime/web.pid" \
    "${runtime_root}/web.pid"; do
    if [[ -f "$pidfile" ]]; then
      pid="$(<"$pidfile")"
      if kill -0 "$pid" 2>/dev/null; then
        printf 'Stopping previous project-owned web server (pid %s) ...\n' "$pid" >&2
        kill "$pid" || true
        for _ in $(seq 1 40); do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.25
        done
      fi
      break
    fi
  done
fi

# ---- 1. Network preflight ------------------------------------------------ #
if [[ ! -r /sys/class/net/enp6s0/carrier ]] \
  || [[ "$(< /sys/class/net/enp6s0/carrier)" != "1" ]]; then
  printf 'WARNING: enp6s0 has no Ethernet carrier; camera/motion will be unavailable.\n' >&2
fi

# ---- 2. Source ROS environment for the worker subprocess ----------------- #
source "${script_dir}/setup_environment.sh"

# ---- 3. Camera bridge check (read-only) ---------------------------------- #
if ! ros2 topic list 2>/dev/null | grep -q '^/camera/front/image_raw/compressed$'; then
  printf 'WARNING: /camera/front/image_raw/compressed not found.\n' >&2
  printf '         Start the read-only perception stack first:\n' >&2
  printf '           bash %s/start_live_perception.sh\n' "${script_dir}" >&2
fi

# ---- 4. Autonomous motion authorization ---------------------------------- #
if [[ "$ENABLE_MOTION" == 1 && "$MOCK" == 0 ]]; then
  for service in /go2w/motion /go2w/arm /go2w/emergency_stop; do
    if ! ros2 service list 2>/dev/null | grep -qx "${service}"; then
      printf 'ERROR: %s is not available; autonomous motion cannot start.\n' "${service}" >&2
      exit 2
    fi
  done
  if [[ "${GO2W_AREA_CLEARED:-}" != "I_HAVE_CLEARED_THE_AREA" ]]; then
    printf 'Autonomous motion requested. You must keep a level, dry, obstacle-free\n' >&2
    printf 'area (>=2 m) and hold the remote emergency stop. Turns are capped at\n' >&2
    printf '<=30 deg, forward steps at <=0.30 m through the existing motion gate.\n' >&2
    read -r -p 'Type I_CONFIRM to authorize autonomous motion: ' answer
    if [[ "$answer" != "I_CONFIRM" ]]; then
      printf 'aborted.\n' >&2
      exit 2
    fi
  fi
fi

# ---- 5. Resolve the Conda Python for Web/LLM ----------------------------- #
conda_python=""
for candidate in \
  /home/brov/miniconda3/envs/go2_robot_scene_demo/bin/python \
  "${CONDA_PREFIX}/bin/python"; do
  if [[ -x "$candidate" ]]; then
    conda_python="$candidate"
    break
  fi
done
if [[ -z "$conda_python" ]]; then
  printf 'ERROR: go2_robot_scene_demo conda environment not found.\n' >&2
  exit 2
fi

# ---- 6. Idempotently add FastAPI/uvicorn --------------------------------- #
if ! "$conda_python" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  printf 'Installing fastapi + uvicorn into go2_robot_scene_demo...\n' >&2
  "$conda_python" -m pip install --quiet fastapi uvicorn
fi

# ---- 7. Launch the Web server (spawns ROS worker + search worker) -------- #
cd "${project_root}"
export MANUAL_DEMO_RUNTIME_DIR="${MANUAL_DEMO_RUNTIME_DIR:-outputs/manual_web_demo/runtime}"
export MANUAL_DEMO_LOGS_DIR="${MANUAL_DEMO_LOGS_DIR:-outputs/manual_web_demo/logs}"
export AUTONOMOUS_SEARCH_RUNTIME_DIR="${runtime_root}"
export AUTONOMOUS_SEARCH_LOGS_DIR="${log_root}"
if [[ "$MOCK" == 1 ]]; then
  export AUTONOMOUS_SEARCH_DEFAULT_BACKEND="mock"
  export AUTONOMOUS_SEARCH_ENABLE_AUTONOMOUS_MOTION="0"
else
  export AUTONOMOUS_SEARCH_DEFAULT_BACKEND="go2w_experimental"
  export AUTONOMOUS_SEARCH_ENABLE_AUTONOMOUS_MOTION="${ENABLE_MOTION}"
fi
setsid "$conda_python" -m uvicorn app.manual_web_demo.web_server:app \
  --host "${host}" --port "${port}" \
  > "${log_root}/web_server.log" 2>&1 &
printf '%s\n' "$!" > "${runtime_root}/web.pid"

# ---- 8. Wait for the API to become ready --------------------------------- #
ready=0
for _ in $(seq 1 60); do
  if curl -fsS "http://${host}:${port}/api/status" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$(<"${runtime_root}/web.pid")" 2>/dev/null; then
    break
  fi
  sleep 0.25
done

if [[ "$ready" != 1 ]]; then
  printf 'ERROR: Web server did not become ready. See %s/web_server.log\n' "${log_root}" >&2
  exit 1
fi

# ---- 9. Open the browser ------------------------------------------------- #
printf 'Go2-W Autonomous Semantic Search WebUI: http://%s:%s\n' "${host}" "${port}"
printf 'Search motion: %s\n' "$([[ "$ENABLE_MOTION" == 1 && "$MOCK" == 0 ]] && echo ENABLED || echo DISABLED/read-only)"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://${host}:${port}" >/dev/null 2>&1 || true
fi
