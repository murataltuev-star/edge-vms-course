# Module 11 — ClusterVMS: Workers That Outlive Their Server

[Module 10](../М10_NodeVMS/module-design.md) built the platform's shape on one box: a **controller** that is the only writer of configuration, a **worker** — DriverPack — running pipelines against its assignment, an **archive resource** on the box's disks, and the platform's two stores underneath. This module runs that shape across several servers and makes it survive any one of them dying: the worker moves and its cameras go with it, the resource stays and its footage with it, and the controller is not consulted — because failover rewrites nothing.

Five lessons in which a server is pulled from the wall and, within a number of seconds you measured, its worker is recording again on another server into another resource — with an edit made *during* the failover already there, because configuration never left the cluster's raft — and when the dead server comes back believing its old worker still owns those cameras, the archive is provably intact.

The full design brief is in [`module-design.md`](module-design.md); the orchestrator choice and its licence are in [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md).

> **Where the module stands (12 September 2026).** The design record is rewritten to *workers, resources, one controller* (*2c*). The five lessons below and the code in `clustervms/` were written for the earlier Node model — a recorder with its own Postgres and its own disk — and are being rewritten to the record. Their mechanisms survive unchanged (the epoch by CAS, the lease on a monotonic clock, placement with its property tests, the `disconnect` numbers); what changes is what a Node was: the worker moves, the resource stays, and configuration no longer has to travel at all.

## The thesis

| | Worker | Resource | Controller |
|---|---|---|---|
| What it is | DriverPack with N cameras assigned | the archive on a server's disks; a GPU; a camera-VLAN NIC | the only writer of `vms/*` |
| How many | 1+, by workload | N, one per eligible server | one — and safe at two |
| Identity | stable, in a Variable, never an allocation index | the server's | none: computation over the stores |
| Moves? | yes — Nomad reschedules it | **never** | not needed to move anything |
| When it is down | its cameras pause until it is rescheduled | that server's footage is unavailable, not lost | edits stop; nothing running stops |

> **The controller writes, the platform stores, the worker reads its share.** Because camera 7 is assigned to worker `w-3` in the cluster's raft rather than to Server A, failover rewrites nothing: Nomad reschedules `w-3` and it reads the same assignment from the same raft. What was on Server A is a resource, and a resource stays.

**Cluster is not domain, and they are different sizes.** A cluster is servers close enough to share a network you would bet recording on — one LAN, one room; that boundary is physics. A domain is clusters under one directory and one signer; that boundary is administration. **A worker fails over within its cluster and never across one**, and [М12](../М12_DomainVMS/README.md) is where several clusters meet.

## Lessons

*As written for the Node model; each row names the mechanism that survives the rewrite.*

| # | Lesson | You'll be able to... |
|---|---|---|
| 1 | [When One Box Isn't Enough](01-when-one-box-isnt-enough.md) | Name what forces a second server; argue why a scheduler on a single appliance costs surface and buys nothing; bring up a three-server Nomad cluster; measure a shard's baseline and increment in PSS and derive the unit of placement. |
| 2 | [The Node as an Allocation](02-the-node-as-an-allocation.md) | Translate a Quadlet unit into a jobspec; say which drivers are plugins and what `exec2` demands of the OS; explain why the allocation index is never identity; give a Node its identity through a Variable and reschedule it to prove it travelled; state why configuration does **not** go there. |
| 3 | [Making a Node's State Outlive Its Server](03-making-a-nodes-state-outlive-its-server.md) | Separate what travels from what stays; explain why shared storage cannot fail over unattended; walk the six-step restore; state the publication order and why; choose the acknowledgement rule; **measure the RPO** and move it. |
| 4 | [Failover, and the Two Instances of One Node](04-failover-and-the-two-instances-of-one-node.md) | Distinguish restart from reschedule and fix the `disconnect` default; put a fencing token in the archive path and prove a resumed zombie is harmless; show why a lock is not a token; issue epochs by check-and-set; state the lease rule on a monotonic clock; export the module's two numbers. |
| 5 | [The Cluster Directory, and Where a Camera Goes](05-the-cluster-directory-and-where-a-camera-goes.md) | Answer *where is camera 7* in one scan and say why it is current; place a camera by measured capacity under constraints; state the stability rule and test it as a property; show the tidy rebalance that breaks it; argue why not consistent hashing. |

## The demo the module is built backwards from

Four Nodes across three servers, two hundred cameras. Then:

```bash
# pull the power on the server running Node 3
```

Node 3 reappears on another server with its configuration intact and resumes its fifty cameras. Footage recorded before the failure stays on the dead server's disks and the console says so. When the dead server returns, its old instance of Node 3 wakes up and tries to keep writing — into `epoch-000005`, a path the index no longer references — **and the archive is intact, provably**: every segment in `epoch-000006` verifies, and `node_epoch_conflicts` moved from zero.

## What you can verify without hardware

More than the subject suggests, because the correctness core has nothing to do with video:

**Runs anywhere** — [`reference/`](reference/README.md), plain Python, milliseconds: the zombie writer with two real processes and `kill -STOP`/`kill -CONT`, fenced and unfenced; the lease state machine on a monotonic clock with the margins' arithmetic; the CAS epoch issuer raced by four threads, and the variable lock's opaque IDs beside it; the six-step restore against fakes; the RPO measured across a thousand random failovers; placement with its property tests. Every number printed in Lessons 3–5 came out of these scripts.

**Needs the bench** — three VMs and Nomad ≥ 1.8.0: cluster formation, `nomad job validate`, the Podman driver, draining, the `disconnect` block, and the power pull itself. The jobspec and agent configurations in `reference/` are written to the documentation and validated against nothing here; the first `nomad job run` belongs to you.

Three things in this module were checked against the projects' own trackers rather than taken on trust, and each lesson says which:

- **Shared storage cannot fail over unattended under Nomad** — [#12118](https://github.com/hashicorp/nomad/issues/12118), open: the volume stays attached to the dead client.
- **The allocation index has had a duplicate-index bug** — [#10727](https://github.com/hashicorp/nomad/issues/10727), fixed, and a label was never promised to carry correctness.
- **A Nomad variable lock's ID is an opaque UUID** — the Locks API; it is the lock Kleppmann's fencing-token argument is about.

## The code, whole

[`clustervms/`](./clustervms/README.md) is the five lessons as one runnable package, built **on** М9's `nodevms/` — `ClusterAppHost` subclasses М9's AppHost and adds the prologue (identity, restore, epoch, lease) and three tasks (publish, lease, heartbeat). `reference/` proves each mechanism in isolation; `clustervms/` is the mechanisms wired into the Node, with the jobspec renderer, the agent configurations, the MinIO job and the placement tool. Its 29 tests run with no Nomad and no Postgres:

```bash
cd clustervms && python3 tests/run.py
```

[`clustervms-go/`](./clustervms-go/README.md) is the same package in Go — every mechanism, the 29 tests ported unchanged in meaning plus a cross-language restore, and `measure.sh`, which puts the whole Node in each language at idle and times the six things it does. The numbers are in Lesson 5 (*The module in Go, measured*): 7.1 MB against 28.5 MB at idle, and within 2× on the work — the win М9 named, and only that one.

```bash
cd clustervms-go && go test ./cluster/          # 30 tests
```

## The two numbers this module exports

| Signal | Is | Report |
|---|---|---|
| **`node_failover_seconds`** | power pulled → recording resumed: the product's **RTO** | the **worst case**, never an average — the customer asks *how long could my site be dark* |
| **`node_epoch_conflicts`** | a stale instance fenced at the archive | zero forever on a healthy cluster; **alarm on it anyway** — the day it moves, something the design said was impossible has happened |

## Where this goes

Everything here holds inside one server room, on one raft: identity, the epoch and the directory are consistent because there is exactly one log.

[**М12 — DomainVMS**](../М12_DomainVMS/module-design.md) adds the second cluster, and the first thing it has to say is what was lost at the boundary. There is no raft spanning clusters, so the directory of directories cannot be consistent; a Node still never crosses from one cluster to another; and what the level above can do is know which cluster a site belongs to, sign for the whole estate, and stay up — or be down — without the clusters noticing.
