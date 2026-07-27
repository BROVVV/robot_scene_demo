#!/usr/bin/env bash
set -euo pipefail
if [[ "${NAV2_ALLOW_EXECUTE:-false}" != "true" ]]; then
  echo "NAV2_ALLOW_EXECUTE 必须显式设为 true" >&2; exit 2
fi
python run_demo.py --mock --enable-nav2 --nav2-mode execute \
  --nav2-goal-x "${1:-1.0}" --nav2-goal-y "${2:-0.0}" --nav2-goal-yaw "${3:-0.0}" \
  --nav2-use-current-start --nav2-allow-execute --nav2-safety-confirmed \
  --nav2-footprint-confirmed --nav2-estop-confirmed --nav2-wait
