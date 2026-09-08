#!/usr/bin/env bash
# Lesson 19, Steps 5 and 8 — pull the cable, wait, put it back.
#   bench/outage.sh 600        # ten minutes
#   bench/outage.sh off | on   # by hand
# Also the "pulled plug" of Lesson 18, Step 6:  bench/outage.sh power-cut
set -euo pipefail
BENCH="${BENCH:-$HOME/edge-bench}"
SOCK="$BENCH/monitor.sock"
mon() { printf '%s\n' "$1" | socat - UNIX-CONNECT:"$SOCK" >/dev/null; }

case "${1:-}" in
  off) mon "set_link nic0 off"; echo "link down";;
  on)  mon "set_link nic0 on";  echo "link up";;
  power-cut) pkill -9 qemu-system-x86_64; echo "power cut. bench/boot.sh to power on";;
  ''|*[!0-9]*) echo "usage: $0 <seconds> | off | on | power-cut" >&2; exit 2;;
  *)   mon "set_link nic0 off"; echo "link down for $1 s"; sleep "$1"; mon "set_link nic0 on"; echo "link up";;
esac
