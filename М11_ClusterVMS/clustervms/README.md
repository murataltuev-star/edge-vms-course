# ClusterVMS — the М11 project, whole

What a Node needs to outlive its server, built **on** М10's `nodevms/` rather
than beside it: `cluster.apphost.ClusterAppHost` subclasses М10's AppHost and
adds the prologue and three tasks. Everything М10 does — reconcile, pump
buses, report, retention, the console — is inherited unchanged.

```
clustervms/
  cluster/
    variables.py     Nomad Variables over HTTP with the task's own token; and a fake with the promised semantics (ModifyIndex, cas, 409, ACL)
    objectstore.py   the restore point: HTTP PUT/GET (MinIO), or a directory
    identity.py      L2 — who am I: the Variable the scheduler delivered, never the allocation index
    epoch.py         L4 — next_epoch by CAS; Lease on a monotonic clock, renewal = reading my own epoch
    configio.py      L3 — what travels: the configuration as one blob; dump/restore on М10's PgStore
    publish.py       L3 — object first, then the Variable; publish on change with a floor; `replicated`
    rehydrate.py     L3 — the six steps; `unconfigured`; a dangling pointer refused
    directory.py     L5 — scan nodes/*: where is camera 7
    placement.py     L5 — capacity measured, constraints as labels, placement STORED in placement/<camera>, budgeted rebalance
    reindex.py       L4 — files back into rows: a fenced instance's footage keeps its epoch; a returned server's archive is rebuilt
    metrics.py       L4 — node_failover_seconds{kind="worst"}, node_epoch_conflicts, appended to М10's /metrics
    console.py       /cluster/node, /cluster/directory, /cluster/where/{id}
    apphost.py       the ClusterAppHost: prologue → publish / lease / heartbeat tasks; fence()
  deploy/
    server.hcl, client.hcl       L1 — three servers, ACLs on, data_dir on /data, meta.vlans, the Podman plugin
    render.py                    L2 — one jobspec per Node (job name = identity), its ACL policy, its bootstrap Variables
    node-3-policy.hcl            L2 — one writer per key
    minio.nomad.hcl              L3 — the object store on the cluster's own servers
    Containerfile                the image the job runs: nodevms + cluster
  tools/place.py                 L5 — the placement service as a command
  tests/                         27 tests, no Nomad, no Postgres, milliseconds: python3 tests/run.py
```

## What happens when the Node starts

```
1. migrate                        М10's runner; on a fresh server the database is empty
2. identity.from_environment      NODE_ID, CONFIG_OBJECT, CONFIG_REVISION, CAMERA_IDS — from the template; the Variable is re-read as authoritative
3. rehydrate                      empty + seen before → fetch the object, check its revision, restore
                                  empty + never seen  → `unconfigured`; invent nothing
                                  not empty           → a restart on the same server; nothing to do
4. next_epoch by CAS              nodes/<node>/epoch; the loser of the race re-reads and goes again
5. EPOCH into the archive path    every new segment lands in /data/archive/<cam>/e<epoch>/ — М10's `e1` was for this
6. Lease(ttl, margin)             may_write while now − last_renewal < ttl − margin, on a monotonic clock
   then М10's loop, plus:
   publish()     each second: if the local revision moved and the floor has passed — object, then Variable (cas)
   lease_task()  every (ttl − margin)/3: read my epoch. Moved → FENCED: stop every pipeline, start nothing, count it
   heartbeat()   every 30 s: a wall-clock timestamp in nodes/<node>/heartbeat
   reindex()     at start and every 10 min: segment files with no index row become rows, epoch from the path
```

`node_failover_seconds` is recording-resumed minus the old instance's last heartbeat, recorded on the first pass that starts a pipeline after a restore, and kept as `last` and `worst` in `nodes/<node>/failover`. `node_epoch_conflicts` is the lease's count of finding a foreign epoch in its own Variable; it should be zero forever.

## Bringing up a Node (Lessons 1 and 2)

```bash
# the cluster: deploy/server.hcl on three servers, deploy/client.hcl on every server that runs Nodes
nomad server members && nomad node status
nomad acl bootstrap                                        # keep the token somewhere that is not a lesson

# the object store on the cluster's own disks
nomad var put cluster/minio user=restore password=$(openssl rand -hex 12)
nomad job run deploy/minio.nomad.hcl                       # then create the `cluster-restore` bucket with an anonymous rw policy

# the image (from the course root)
podman build -f М11_ClusterVMS/clustervms/deploy/Containerfile -t localhost/clustervms-apphost:latest .

# one Node
python3 deploy/render.py node-3 --bootstrap | sh          # its Variables and ACL binding
python3 deploy/render.py node-3 --policy   > node-3-policy.hcl
python3 deploy/render.py node-3 --vlan cctv-a --memory 2048 > node-3.nomad.hcl
nomad job validate node-3.nomad.hcl && nomad job run node-3.nomad.hcl
curl -s http://<its server>:8080/cluster/node
```

Add cameras through М10's console (`POST /cameras`) or `psql`; within a
second the Node publishes `node-3/rev-N` to the object store and points its
Variable at it. `GET /cluster/where/7` answers from the directory.

## The failover (Lessons 3 and 4)

```bash
nomad node drain -enable -yes <server>        # planned: the Node moves, its open segment finalizes
# or pull the power on the server running node-3
nomad job status node-3                       # a new allocation elsewhere
curl -s http://<new server>:8080/cluster/node # {"restore":"restored","epoch":2,...,"failover":{"last":..,"worst":..}}
curl -s http://<new server>:8080/metrics | grep ^node_
```

Bring the old server back. Its old instance renews its lease, finds epoch 2
in its Variable, logs `FENCED`, stops every pipeline, and
`node_epoch_conflicts` moves to 1. Its post-partition segments are in
`e1/`, unindexed; the live archive is in `e2/`.

## What was verified where

- **Run, output real:** `tests/run.py` — 27 tests against the in-memory
  raft and a directory object store: the CAS issuer raced by four threads;
  one writer per key; the lease fencing on a foreign epoch and expiring at
  TTL − margin on an injected clock; publish order (object, then Variable),
  the floor, `replicated`, the store unreachable never blocking an edit;
  the six-step restore, the RPO as the unpublished edit, `unconfigured`,
  the dangling pointer and the revision mismatch refused, a same-server
  restart restoring nothing; the directory scan with its cache and the
  two-claimants error; placement stored and read back by a fresh process,
  the stability rule, the tidy rebalance that fails it, budgeted rebalance
  with reasons; and the whole `ClusterAppHost` with a fake actuator — the
  prologue restoring and recording into a new epoch with the failover time
  recorded, and **the zombie fenced** when the replacement takes the epoch.
  the reindex sweep — fenced segments back with their epoch, an open segment left alone, a returned archive rebuilt, idempotent.
  `restore_config`'s SQL ran against PostgreSQL 16.13: revision 812 comes
  back exactly, and the sequence moves past the restored ids.
- **Written to the documentation, not executed here:** `NomadVariables`
  (the HTTP calls, the `?cas=` query, the 409 and 403 mappings),
  `HttpObjectStore` against MinIO, the rendered jobspec, `minio.nomad.hcl`,
  the ACL policy binding, and the Containerfile. No Nomad binary was
  reachable from the authoring sandbox. `nomad job validate` is the first
  thing to run, and `deploy/render.py node-3 --bootstrap` the second.

## Known gaps, named

- **The ACL scoping of Variable writes per job is the module's open question.** `render.py --bootstrap` prints the `nomad acl policy apply -job` line the docs describe; whether the task's workload-identity token then gets exactly `nodes/node-3*` and nothing else is what to verify on the bench before trusting one-writer-per-key.
- The lease numbers are the module's decision (Lesson 4): TTL 30 s, margin 5 s, `stop_on_client_after` 25 s, `lost_after` 45 s. `render.py` ships them as defaults; measure `node_failover_seconds` on the bench before changing them.
- `reindex()` rebuilds a returned server's index from its segments and re-indexes a fenced instance's footage with its epoch (Lesson 4's decision: re-index, never delete). The console shows a row whose epoch is older than the Node's current one as *recorded by a fenced instance*; the flag is the `epoch` column М10's `timeline` already returns.
- A camera move decided by `tools/place.py` is recorded in `placement/<camera>`; the Nodes do not yet *act* on it — the wire from a placement row to М10's `cameras` table on the losing and gaining Nodes is the two-writer handover Lesson 5 describes, and it belongs with М12's placement-at-the-level-above.
- `HttpObjectStore` assumes anonymous PUT/GET on the bucket; a signed-S3 adapter is twenty lines of boto3 on the same two methods, left out to keep the Node image dependency-free.
- `HEARTBEAT_INTERVAL=30` is a raft write per Node per 30 s. At a thousand Nodes that is 33 writes/s to the servers; the interval should scale with the cluster, and `node_failover_seconds` is measured at that granularity.
