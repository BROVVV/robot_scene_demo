#!/usr/bin/env bash
# Start the long-running SiliconFlow VLM daemon (Conda Python).
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/../.." && pwd)"
conda_python="${SILICONFLOW_PYTHON:-${GO2W_CONDA_PYTHON:-/home/brov/miniconda3/envs/go2_robot_scene_demo/bin/python}}"
if [[ ! -x "${conda_python}" && -x "${project_root}/.venv/bin/python" ]]; then
  conda_python="${project_root}/.venv/bin/python"
fi
socket_path="${project_root}/runtime/go2w/siliconflow_vlm.sock"
pid_file="${project_root}/runtime/go2w/pids/siliconflow_vlm.pid"

# The robot has no working Internet gateway of its own.  The operator PC
# exposes a persistent reverse SOCKS tunnel on this loopback port.  Override
# any short-lived SSH proxy inherited from an interactive login.
vlm_proxy_host="${GO2W_VLM_PROXY_HOST:-127.0.0.1}"
vlm_proxy_port="${GO2W_VLM_PROXY_PORT:-17892}"
vlm_proxy_url="${GO2W_VLM_PROXY_URL:-socks5://${vlm_proxy_host}:${vlm_proxy_port}}"
export HTTP_PROXY="${vlm_proxy_url}"
export HTTPS_PROXY="${vlm_proxy_url}"
export ALL_PROXY="${vlm_proxy_url}"
export http_proxy="${vlm_proxy_url}"
export https_proxy="${vlm_proxy_url}"
export all_proxy="${vlm_proxy_url}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1,192.168.123.0/24,172.17.0.0/16}"
export no_proxy="${NO_PROXY}"

mkdir -p "$(dirname "${pid_file}")" "$(dirname "${socket_path}")" "${project_root}/runtime/go2w/sessions"

if [[ -S "${socket_path}" ]]; then
  old_pid=""
  if [[ -f "${pid_file}" ]]; then
    old_pid="$(tr -cd '0-9' < "${pid_file}")"
  fi
  if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
    echo "VLM daemon already running (pid ${old_pid})" >&2
    exit 0
  fi
  # Stale socket: remove it so the daemon can bind again.
  rm -f "${socket_path}"
  rm -f "${pid_file}"
fi

if [[ -f "${pid_file}" ]]; then
  old_pid="$(tr -cd '0-9' < "${pid_file}")"
  if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
    echo "VLM daemon already running (pid ${old_pid})" >&2
    exit 0
  fi
  rm -f "${pid_file}"
fi

if [[ ! "${vlm_proxy_host}" =~ ^[A-Za-z0-9.:-]+$ || ! "${vlm_proxy_port}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: invalid GO2W VLM proxy host or port" >&2
  exit 2
fi
if ! timeout 2 bash -c "exec 3<>/dev/tcp/${vlm_proxy_host}/${vlm_proxy_port}" 2>/dev/null; then
  echo "ERROR: persistent VLM proxy is unavailable at ${vlm_proxy_host}:${vlm_proxy_port}" >&2
  echo "Start robotscene-vlm-tunnel.service on the operator PC." >&2
  exit 2
fi

nohup "${conda_python}" "${project_root}/app/detectors/siliconflow_vision_daemon.py" \
  --socket "${socket_path}" \
  >"${project_root}/runtime/go2w/sessions/siliconflow_vlm_daemon.log" 2>&1 &
echo $! > "${pid_file}"
# Wait for the socket to appear.
for _ in $(seq 1 20); do
  if [[ -S "${socket_path}" ]]; then
    echo "VLM daemon started (pid $(cat "${pid_file}"))"
    exit 0
  fi
  sleep 0.2
done
echo "ERROR: VLM daemon did not create socket" >&2
exit 1
