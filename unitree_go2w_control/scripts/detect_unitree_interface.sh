#!/usr/bin/env bash
set -Eeuo pipefail

ROBOT_IP="${UNITREE_ROBOT_IP:-192.168.123.18}"
route_line="$(ip route get "$ROBOT_IP" 2>/dev/null | head -n 1)"
interface="$(awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}' <<<"$route_line")"

if [[ -z "$interface" || "$interface" == "lo" ]]; then
  # When this script runs on the robot itself, the route to its own IP goes
  # via lo.  Fall back to the first non-loopback interface that actually owns
  # the robot 192.168.123.18 address.
  interface="$(ip -4 -o address show 2>/dev/null \
    | awk -v ip="$ROBOT_IP" '$4 ~ "^" ip "/" {sub(/^[0-9]+: /, "", $2); print $2; exit}')"
fi
if [[ -z "$interface" || "$interface" == "lo" ]]; then
  printf 'ERROR: cannot resolve a usable interface for %s\n' "$ROBOT_IP" >&2
  exit 1
fi

printf '%s\n' "$interface"
