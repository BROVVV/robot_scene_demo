#!/usr/bin/env bash
set -euo pipefail

# Go2-W Manual WASD+QE Web Demo launcher (plan book §36).
#
#   bash scripts/go2w/start_manual_web_demo.sh                 # camera + LLM, motion disabled
#   bash scripts/go2w/start_manual_web_demo.sh --enable-motion # allow WASD+QE through /go2w/motion
#
# This script never starts Nav2, UniGoal, the Pandar driver or Point-LIO.

ENABLE_MOTION=0
for arg in "$@"; do
  case "$arg" in
    --enable-motion) ENABLE_MOTION=1 ;;
    *) printf 'ERROR: unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
runtime_root="${project_root}/outputs/manual_web_demo/runtime"
log_root="${project_root}/outputs/manual_web_demo/logs"
mkdir -p "${runtime_root}" "${log_root}"

host="${MANUAL_DEMO_HOST:-127.0.0.1}"
port="${MANUAL_DEMO_PORT:-8765}"

# ---- 1. Network preflight ------------------------------------------- #
if [[ ! -r /sys/class/net/enp6s0/carrier ]] \
  || [[ "$(< /sys/class/net/enp6s0/carrier)" != "1" ]]; then
  printf 'WARNING: enp6s0 has no Ethernet carrier; camera/motion will be unavailable.\n' >&2
fi

# ---- 2. Source ROS environment for the worker subprocess ------------- #
# shellcheck source=setup_environment.sh
source "${script_dir}/setup_environment.sh"

# ---- 3. Camera bridge check (read-only) ------------------------------ #
if ! ros2 topic list 2>/dev/null | grep -q '^/camera/front/image_raw/compressed$'; then
  printf 'WARNING: /camera/front/image_raw/compressed not found.\n' >&2
  printf '         Start the read-only perception stack first:\n' >&2
  printf '           bash %s/start_live_perception.sh\n' "${script_dir}" >&2
fi

# ---- 4. Motion stack check ------------------------------------------ #
if [[ "$ENABLE_MOTION" == 1 ]]; then
  for service in /go2w/motion /go2w/arm /go2w/emergency_stop; do
    if ! ros2 service list 2>/dev/null | grep -qx "${service}"; then
      printf 'WARNING: %s is not available; motion will be OFFLINE.\n' "${service}" >&2
    fi
  done
  if [[ "${GO2W_AREA_CLEARED:-}" != "I_HAVE_CLEARED_THE_AREA" ]]; then
    printf 'Motion requested. You must keep a level, dry, obstacle-free area (>=2 m)\n' >&2
    printf 'and hold the remote emergency stop.\n' >&2
    read -r -p 'Type I_CONFIRM to authorize motion: ' answer
    if [[ "$answer" != "I_CONFIRM" ]]; then
      printf 'aborted.\n' >&2
      exit 2
    fi
  fi
fi

# ---- 5. Resolve the Conda Python for Web/LLM ------------------------- #
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

# ---- 6. Idempotently add FastAPI/uvicorn ----------------------------- #
if ! "$conda_python" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  printf 'Installing fastapi + uvicorn into go2_robot_scene_demo...\n' >&2
  "$conda_python" -m pip install --quiet fastapi uvicorn
fi

# ---- 7. Launch the Web server (spawns the ROS worker) ---------------- #
cd "${project_root}"
export MANUAL_DEMO_RUNTIME_DIR="${MANUAL_DEMO_RUNTIME_DIR:-outputs/manual_web_demo/runtime}"
export MANUAL_DEMO_LOGS_DIR="${MANUAL_DEMO_LOGS_DIR:-outputs/manual_web_demo/logs}"
setsid "$conda_python" -m uvicorn app.manual_web_demo.web_server:app \
  --host "${host}" --port "${port}" \
  > "${log_root}/web_server.log" 2>&1 &
printf '%s\n' "$!" > "${runtime_root}/web.pid"

# ---- 8. Wait for the API to become ready ------------------------------ #
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

# ---- 9. Open the browser --------------------------------------------- #
printf 'Go2-W Manual WASD+QE Demo: http://%s:%s\n' "${host}" "${port}"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://${host}:${port}" >/dev/null 2>&1 || true
fi
