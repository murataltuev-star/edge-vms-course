#!/usr/bin/env bash
# М11 — the checks that need a real cluster, in one run. Prints PASS/FAIL per item.
#
#   deploy/verify-bench.sh node-3          # against the Lesson 1 cluster, with NOMAD_ADDR/NOMAD_TOKEN (management) set
#
# 1. Nomad >= 1.8.0 (the disconnect block; 1.7.x is EOL and BUSL-first)
# 2. three servers, one leader; the Podman driver healthy on every client
# 3. the rendered jobspec validates
# 4. THE OPEN QUESTION: an ACL policy bound to the job scopes Variable writes
#    to nodes/<node>* — write own path succeeds, another Node's path is 403
# 5. the same, from INSIDE the allocation with the task's own workload-identity token
set -u
NODE="${1:?node id, e.g. node-3}"
OTHER="node-999"
HERE="$(cd "$(dirname "$0")" && pwd)"
pass=0; fail=0
ok()   { echo "  PASS  $*"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $*"; fail=$((fail+1)); }

# 1
ver="$(nomad version | head -1 | sed -E 's/.*v([0-9]+\.[0-9]+\.[0-9]+).*/\1/')"
if [ "$(printf '%s\n1.8.0\n' "$ver" | sort -V | head -1)" = "1.8.0" ]; then ok "nomad $ver >= 1.8.0"; else bad "nomad $ver < 1.8.0 — the disconnect block does not exist"; fi

# 2
leaders="$(nomad server members 2>/dev/null | awk 'NR>1 && $5=="true"' | wc -l)"
servers="$(nomad server members 2>/dev/null | awk 'NR>1' | wc -l)"
[ "$servers" -ge 3 ] && [ "$leaders" -eq 1 ] && ok "$servers servers, 1 leader" || bad "$servers servers, $leaders leaders"
for id in $(nomad node status -quiet 2>/dev/null); do
  if nomad node status -verbose "$id" 2>/dev/null | grep -qE '^podman +true'; then ok "podman driver healthy on $id"; else bad "podman driver NOT healthy on $id"; fi
done

# 3
python3 "$HERE/render.py" "$NODE" > "/tmp/$NODE.nomad.hcl"
nomad job validate "/tmp/$NODE.nomad.hcl" >/dev/null 2>&1 && ok "jobspec validates" || bad "jobspec does not validate: $(nomad job validate "/tmp/$NODE.nomad.hcl" 2>&1 | tail -1)"

# 4 — policy semantics with a token that carries ONLY this policy
python3 "$HERE/render.py" "$NODE" --policy > "/tmp/$NODE-policy.hcl"
nomad acl policy apply -description "$NODE" "$NODE" "/tmp/$NODE-policy.hcl" >/dev/null 2>&1
tok="$(nomad acl token create -type client -policy "$NODE" -ttl 10m -json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["SecretID"])')"
if [ -n "$tok" ]; then
  if NOMAD_TOKEN="$tok" nomad var put -force "nodes/$NODE/verify" probe=1 >/dev/null 2>&1; then ok "policy: token may write nodes/$NODE/*"; else bad "policy: token cannot write its OWN path"; fi
  if NOMAD_TOKEN="$tok" nomad var put -force "nodes/$OTHER" probe=1 >/dev/null 2>&1; then bad "policy: token wrote nodes/$OTHER — one-writer-per-key is NOT enforced"; else ok "policy: nodes/$OTHER refused (403)"; fi
  if NOMAD_TOKEN="$tok" nomad var get "nodes/$NODE/verify" >/dev/null 2>&1; then ok "policy: read of own path"; fi
  nomad var purge "nodes/$NODE/verify" >/dev/null 2>&1
else
  bad "could not create a client token with policy $NODE (ACLs bootstrapped? NOMAD_TOKEN set?)"
fi

# 5 — the binding to the JOB's workload identity, which is what the product relies on
nomad acl policy apply -namespace default -job "$NODE" "$NODE" "/tmp/$NODE-policy.hcl" >/dev/null 2>&1 && ok "policy bound to job $NODE" || bad "policy binding to job failed"
alloc="$(nomad job allocs -json "$NODE" 2>/dev/null | python3 -c 'import sys,json;a=[x for x in json.load(sys.stdin) if x["ClientStatus"]=="running"];print(a[0]["ID"] if a else "")')"
if [ -n "$alloc" ]; then
  inside='H="X-Nomad-Token: $NOMAD_TOKEN"; A="${NOMAD_ADDR:-http://127.0.0.1:4646}";
    own=$(curl -s -o /dev/null -w "%{http_code}" -X PUT -H "$H" -d "{\"Items\":{\"probe\":\"1\"}}" "$A/v1/var/nodes/'"$NODE"'/verify");
    oth=$(curl -s -o /dev/null -w "%{http_code}" -X PUT -H "$H" -d "{\"Items\":{\"probe\":\"1\"}}" "$A/v1/var/nodes/'"$OTHER"'");
    echo "own=$own other=$oth"'
  res="$(nomad alloc exec -task apphost "$alloc" sh -c "$inside" 2>/dev/null)"
  case "$res" in
    own=200*other=403*) ok "workload identity: $res — one writer per key holds";;
    *) bad "workload identity: $res (want own=200 other=403)";;
  esac
  nomad var purge "nodes/$NODE/verify" >/dev/null 2>&1
else
  bad "no running allocation of $NODE to test the workload-identity token inside (run the job first)"
fi

echo; echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
