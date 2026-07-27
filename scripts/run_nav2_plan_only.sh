#!/usr/bin/env bash
set -euo pipefail
python run_demo.py --mock --enable-nav2 --nav2-mode plan_only \
  --nav2-goal-x "${1:-1.0}" --nav2-goal-y "${2:-0.0}" --nav2-goal-yaw "${3:-0.0}" \
  --nav2-use-current-start --nav2-wait
