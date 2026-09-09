#!/usr/bin/env bash
# М11 Lesson 4, Step 6 — the power pull, measured. Three runs, worst case kept.
#
#   deploy/failover-drill.sh node-3 <console host:port of any server> [runs]
#
# For each run: find the server running the Node, pull it (a drain with a
# zero deadline stands in for the power cut; use М9's bench/outage.sh
# power-cut for the real thing), wait for the Node to answer elsewhere, read
# node_failover_seconds from /cluster/node, then return the server.
set -u
NODE="${1:?node id}"; ANY="${2:?console host:port of any server, e.g. 10.0.0.11:8080}"; RUNS="${3:-3}"

# helpers: JSON in on stdin, one value out. Kept as functions so the loop
# below has no nested quoting for the shell to trip on.
running_node_id() {           # $1 = a NodeID to exclude, or ""
  EXCLUDE="$1" python3 -c '
import sys, json, os
a = [x for x in json.load(sys.stdin) if x["ClientStatus"] == "running" and x["NodeID"] != os.environ["EXCLUDE"]]
print(a[0]["NodeID"] if a else "")'
}
json_field() {                # $1 = dotted path, e.g. failover.last
  KEY="$1" python3 -c '
import sys, json, os
try:
    v = json.load(sys.stdin)
except ValueError:          # curl failed or the Node is not up yet
    v = {}
for k in os.environ["KEY"].split("."):
    v = v.get(k, 0) if isinstance(v, dict) else 0
print(v)'
}
node_host() { python3 -c 'import sys, json; print(json.load(sys.stdin)["HTTPAddr"].split(":")[0])'; }

worst=0
for i in $(seq 1 "$RUNS"); do
  old="$(nomad job allocs -json "$NODE" | running_node_id "")"
  [ -n "$old" ] || { echo "no running allocation of $NODE"; exit 1; }
  t0=$(date +%s)
  echo "run $i: pulling $old at $(date -u +%H:%M:%S)"
  nomad node drain -enable -deadline 0s -yes "$old" >/dev/null

  new=""
  for _ in $(seq 1 120); do
    new="$(nomad job allocs -json "$NODE" | running_node_id "$old")"
    [ -n "$new" ] && break
    sleep 1
  done
  [ -n "$new" ] || { echo "run $i: no replacement within 120 s"; exit 1; }
  host="$(nomad node status -json "$new" | node_host)"

  j=""
  for _ in $(seq 1 120); do
    j="$(curl -s "http://$host:8080/cluster/node" || true)"
    [ "$(printf '%s' "$j" | json_field restore)" = "restored" ] && break
    sleep 1
  done
  secs="$(printf '%s' "$j" | json_field failover.last)"
  epoch="$(printf '%s' "$j" | json_field epoch)"
  t1=$(date +%s); wall=$((t1 - t0))
  echo "run $i: recording resumed on $new after ${secs}s (wall clock ${wall}s); epoch $epoch"
  worst="$(python3 -c "print(max($worst, $secs))")"

  nomad node drain -disable -yes "$old" >/dev/null
  sleep 20
  conflicts="$(curl -s "http://$ANY/metrics" | awk '/^node_epoch_conflicts/ {print $2; exit}')"
  echo "run $i: $old returned; node_epoch_conflicts: ${conflicts:-?}"
done
echo
echo "node_failover_seconds worst case over $RUNS runs: ${worst}s   <- the datasheet number"
