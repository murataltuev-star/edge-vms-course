# ClusterVMS — the М11 project, whole

М10's shape across several servers, built **on** М10's `vmsnode/` rather than beside it: the same `vmsplatform` contract, the same `vms/` controller, worker and archive resource, imported unchanged. What this package adds is exactly what a cluster adds — Nomad's stores, what an allocation knows about itself, placement under constraints, a resource that has to say it exists, and a timeline that spans servers.

```
clustervms/
  cluster/
    variables.py     L1  Nomad Variables over HTTP with the task's own token (ModifyIndex, cas, 409, 403) — and the fake with the promised semantics
    objectstore.py   L1  MinIO / S3 (s3.py: SigV4, verified against Amazon's worked examples), HTTP, or a directory — put/get/list
    worker.py        L2  the worker as an allocation: the slot from NOMAD_ALLOC_INDEX, the server and its labels from the environment, capacity and headroom in the heartbeat
    controller.py    L5  the controller as a job: placement under label constraints with the server in the reason; `unplaceable`; the snapshot for М12; vms_failover_seconds from the heartbeats
    directory.py     L5  where is camera 7 — one scan of vms/workers/*
    resource.py      L3  the archive resource as a system job: its heartbeat, its manifests, footage and event files served, its policy (repair, then retain)
    eventindex.py    L3  the cluster's event "database", which is a cache: SQLite over every subsystem's buckets on every resource, rebuildable, honest about a silent server; joins subsystems on a `cam` field
    timeline.py      L3  one camera across two resources; the unreachable one named; *unavailable*, never *lost*
    console.py       L5  the cluster console, standard library: /cameras /where /timeline /resources /unplaceable /metrics
    publish.py, configio.py   the first design's Node-shaped snapshot — kept only because М12's fixture reads it; goes with М12's rewrite
    __main__.py      python3 -m cluster worker | controller | resource   (the controller job also runs the eventindex beside the console)
  deploy/
    server.hcl, client.hcl     L1  three servers, ACLs on, meta.labels and meta.archive, the Podman plugin
    vmsworker.nomad.hcl        L2  service, count = N, the `scaling` block on avg(vms_worker_load), the disconnect numbers, kill_timeout for the slot release
    vmscontroller.nomad.hcl    L2  service, count = 1 — safe at two
    vmsarchive.nomad.hcl       L2  system, on meta.archive — the resource
    autoscaler.nomad.hcl       L2  the Nomad Autoscaler (MPL-2.0): the fourth job, and the only thing that changes count
    vmsworker-policy.hcl, vmscontroller-policy.hcl   L2  one writer per key: vms/* for the controller; vms/epoch/* and vms/slots/* for a worker
    minio.nomad.hcl            L1  the object store on the cluster's own servers
    verify-bench.sh            the six checks that need a real cluster, PASS/FAIL — including the ACL from inside an allocation and a scale drill
    failover-drill.sh          L4  the power pull, measured: three runs, worst case kept, the old instance's conflicts counted
    Containerfile              the image: vmsnode + cluster, three entrypoints
  tests/                       27 tests, no Nomad, no MinIO, no GStreamer, milliseconds: python3 tests/run.py
```

## What a cluster adds, and what it does not

| | М10, one box | М11, a cluster | Where |
|---|---|---|---|
| The config store | files with `ModifyIndex` and CAS | Nomad Variables — the same two promises, kept by raft | `variables.py` |
| The object store | a directory | MinIO, SigV4 | `objectstore.py`, `s3.py` |
| A worker's name | `systemd`'s `%i` | `w-<NOMAD_ALLOC_INDEX>`, claimed by CAS — the index is the preference, the Variable the proof | `worker.py` |
| Who decides how many workers | the operator starts units | Nomad runs `count`; the Autoscaler moves it from `vms_worker_load`; **never the controller** | `deploy/vmsworker.nomad.hcl` |
| Placement | most free capacity | most free capacity **among workers whose server can reach the camera** (`labels`) | `controller.py` |
| The archive | a directory on the box | the same directory on *each* server, pinned by a `system` job, with a heartbeat and its manifests served | `resource.py` |
| A timeline | one manifest | merged across the resources that hold the camera; a silent one is named as unreachable | `timeline.py` |
| What leaves the cluster | nothing | one snapshot object for М12's read model — a copy with an age | `controller.py` |
| Events | buckets per unit on the resource, written by the worker holding the epoch, any subsystem | the same, on each server's resource; indexed across the cluster by `eventindex`, a cache | `eventindex.py` |
| The contract, the controller's logic, the worker's loop, the epoch, the lease, the manifest | | **unchanged**: imported from `vmsnode/` | |

## The three lines the tests hold

**Failover rewrites nothing.** `test_the_power_pull`: Server A dies; 48 s later a fresh allocation with the same index claims `w-1`, reads the assignment the controller wrote before the failure, takes the next epoch for each camera and records on Server B — and the edit made *during* the failover is in the rows it read, because the controller's acknowledgement was the CAS commit into raft (`test_an_edit_during_the_failover_is_simply_there`).

**The controller never decides how many workers, or where.** `test_nomad_job_scale_out_then_in`: a new allocation claims a new slot and the waiting camera lands on it; a stopped one releases its slot and its cameras are redistributed; `test_two_allocations_with_one_index_resolve_at_the_cas`: Nomad's documented duplicate-index bug is harmless because the index is not the identity.

**Old footage is unavailable, never lost.** `test_a_timeline_spans_two_resources_and_names_the_unreachable_one`: a camera's manifest lines come from two servers; when one is silent its ranges are listed as unavailable *by name*, and when it returns its manifest came back with its disks — nothing was rebuilt.

## Verified where

The 27 tests ran in the authoring sandbox (Python 3.11) and on the author's machine (3.10), on fakes that implement what Nomad's and S3's documentation promise. `deploy/verify-bench.sh` and `deploy/failover-drill.sh` are what proves the promises against real Nomad: the ACL from inside an allocation, the four jobspecs validating, the scale drill, and the power pull with the worst case kept. `clustervms-go/` is the Go port of the *first* design and stays as its measurement record; its 2c port follows this package.
