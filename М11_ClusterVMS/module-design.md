# М11_ClusterVMS — Module Design

**A Node that outlives the server recording on it.**

М10 built one Node: a database holding what it should be, and a loop making it so. It ran on one box, and if that box died the cameras stopped. This module runs several servers and makes a Node survive any one of them dying — carrying its configuration, its cameras and its archive identity to whatever hardware it lands on.

The organising decision, taken up front because everything depends on it: **a Node owns its own configuration.** Nomad moves the Node; the cameras go with it; nothing rewrites who owns what. That is what makes failover teachable *here* rather than deferred to a coordinating layer: there is no ownership to reassign, so a dead server is a relocation rather than a decision.

> **Scope note.** These four lessons and [М12](../М12_DomainVMS/module-design.md)'s six were one module until this split, and the merge that created it had a good reason: *a cluster and the layer above it are one arc, and splitting them meant teaching the two-level idea twice.* That reason still holds, and the split answers it rather than ignoring it — **the two-level idea is introduced here and collected in М12**, which is the same setup-and-collection the course already runs across module boundaries, from М9's AWS credentials to М13's vault. Lesson 26 says out loud that Nomad places *Nodes* and something above will place *cameras*; М12 Lesson 30 is where that second level arrives and the student is asked to name the difference. Taught inside one module, the two levels blur, because both are "scheduling".

> **Cluster is not domain.** This module builds a **cluster**: the set of servers a scheduler manages. М12 builds a **domain**: the set of Nodes under one directory. They are normally the same machines and they are not the same thing — a cluster answers *where can this run*, a domain answers *what is supposed to be running*. The test: **losing the cluster stops rescheduling; losing the domain stops nothing that is already recording.** This is the fifth pair of words the course keeps apart, after Node/Server/Site, the two orchestrations and the two federations.

---

## The thesis

**A Node is a logical thing, not a server.** М10's Node — its Postgres, its AppHost, its cameras — becomes a Nomad allocation with stable identity. Nomad decides which server runs it. The Node does not change when that answer changes.

| | Node | Server |
|---|---|---|
| What it is | a VMS instance with its own database and cameras | a box with CPUs and disks |
| Identity | stable, assigned once | whatever hardware is available |
| Who decides | an operator, when capacity is bought | Nomad, continuously |
| Owns camera 7 | **yes, permanently** | never |

Because camera 7 belongs to Node N rather than to Server A, **failover rewrites nothing.** Nomad reschedules allocation N onto Server B and Node N carries on being Node N. The question this module actually has to answer is narrower and harder: **what does it take for Node N's state to be there when it arrives?**

### The demo it is built backwards from

Four Nodes across four servers, two hundred cameras. Then:

```
# pull the power on the server running Node 3
```

Node 3 reappears on another server with its configuration intact and resumes its fifty cameras. Footage recorded before the failure stays on the dead server's disks and the console says so. When the dead server returns, its old instance of Node 3 tries to keep writing — **and the archive is intact, provably.**

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Camera ownership | **The Node owns it, permanently** | Failover moves the Node, so nothing rewrites ownership — a dead server is a relocation, not a decision. |
| Node identity | **Stable, carried in a Nomad Variable — never the allocation index** | Variables are built for exactly this and survive rescheduling. The allocation index has had documented uniqueness bugs: fine for a label, never for correctness. |
| Configuration authority | **The Node, replicating one way upward** | Configuration stays next to the software using it, and survives the domain layer being down. |
| Fencing | **At the archive, not at the controller** | You cannot stop a zombie from writing. You can make its writes land where nobody reads. |
| Epoch issuer | **A Nomad Variable with check-and-set** | Atomic, monotonic and raft-replicated, so it survives losing a server. Nomad's *variable lock* is the trap: its lock ID is an opaque UUID, not a fencing token. |
| Orchestrator | **Nomad** | Non-container workloads, Podman kept as the runtime, far smaller operational surface. See [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md). |
| **Minimum version** | **Nomad ≥ 1.8.0. Target 1.10.x LTS or 2.0.x** | The `disconnect` block Lesson 28 is built on arrived in **1.8.0**; before that there is only `max_client_disconnect`/`stop_after_client_disconnect` and **no `reconcile` strategies at all**. Those predecessors were then *removed* in 1.10.0, so writing against 1.7.x teaches syntax that no longer exists. 1.7.x is also EOL with an allocation-directory-escape CVE fixed only in Enterprise. |
| Single-server deployments | **No orchestrator at all** | М9's Quadlet stack is better on one box, and Lesson 25 makes students argue that rather than assert it. |

The decisions about the layer *above* these Nodes — what a directory holds, how it is stored, and who may call it — are [М12's](../М12_DomainVMS/module-design.md).

---

## Prerequisites

- **М10 entire.** What it built on one box is a Node. This module runs several and moves them between servers.
- **Lesson 19** — Quadlet. Lesson 26 maps those units onto a scheduler, which is a translation rather than a rewrite.
- **Lessons 5–6** — signals. `kill -STOP` is the module's most important teaching device.
- **М9's process-model record** — capacity, shard sizing, and why the orchestrator must not own camera lifecycle.

---

## What must outlive a server

Part A's real subject, and the thing that makes failover more than a demo. Node N's data sits on Server A's disk. Nomad moves N to Server B. What comes with it?

| | On the dead server | Recoverable? |
|---|---|---|
| Footage | stays | **No — and it does not need to.** The past stays where it was written; a replacement records the future |
| Archive index | stays | **Yes** — rebuild by scanning segments |
| Events | stays | Yes, or accept the loss; they are observations |
| **Configuration** | stays | **No. It is the source of truth, and losing it loses the Node** |

So exactly one thing must travel, and it is the smallest: **the Node's configuration.** Two ways, and the choice is a real product decision:

| | **2a — shared storage** | **2b — local storage, replicated** |
|---|---|---|
| Mechanism | the Node's database on a CSI volume | local disk; configuration streamed one way to the domain, pulled back on start |
| Fencing | **the storage does it** — exclusive attachment means the old instance cannot write | needs a token issuer |
| **Automatic failover** | **No — see below** | **Yes** |
| Cost | a SAN or NAS: expensive, and a shared failure domain | a replication path, and a token issuer to build |
| Fits | a datacentre, with an operator on call | **an appliance, which is this course's target** |

**2a does not fail over unattended, and this is worth checking before designing around it.** [Nomad issue #12118](https://github.com/hashicorp/nomad/issues/12118) — still open — reports that when a client holding a CSI volume dies, **the volume stays attached to the dead node.** Rescheduling fails with *"volume is already published on another node"*, Nomad's volume watcher cannot force-detach, and the documented workaround is detaching manually through the storage provider's console.

So shared storage buys fencing and **loses** the automatic recovery it was adopted for. That inverts the usual advice: 2a is not the grown-up option that appliances cannot afford — for a box nobody visits it is the *wrong* option, because the failure it is meant to survive ends with a human logging into a SAN console at 3am.

**The course teaches 2b**, and shows 2a as a cautionary comparison rather than an aspiration.

### The directory is each Node's off-box backup

That is the mental model, and it explains the whole arrangement in one line: **a Node publishes its own configuration upward whenever it changes; the directory stores the latest revision per Node and never writes back.**

- **One-way, because a backup does not write back.** There is no merge, no conflict, no election
- **The domain may be down during normal operation**, because you do not need a backup in order to *run*
- **It is required to fail over**, because that is a restore
- **And it has a recovery point objective (RPO)** — the publication interval, and therefore the most recent configuration change an outage may lose — which is a number the product states rather than a surprise it discovers

### The acknowledgement problem

Here is the gap that framing exposes, and it is the honest cost of the availability this design buys. **What is the operator told when they save a camera?**

If the Node acknowledges on local commit and dies before publishing, the operator was told *saved* and the change is gone.

| | Cost |
|---|---|
| Acknowledge only after publishing | Configuration edits now require the directory — the offline-edit advantage is destroyed |
| Acknowledge on local commit, say nothing | Silent data loss on failover |
| **Acknowledge on local commit, and show durability** | The operator sees *saved · not yet replicated* until it lands |

The third needs no new machinery. The module already has `observed_revision >= revision` and a directory that knows how far behind each Node is; the console shows the same thing for configuration that it shows for everything else, and a Node that has been unable to publish for N minutes raises a condition.

### Two gaps that stay open by design

- **A Node the directory has never seen** — brand new, or its first publish never landed — has nothing to restore. It must **not invent a configuration**: it comes up empty, reports *unconfigured*, and waits for an operator or for М12's enrollment
- **The archive index does not come back.** It is large and constantly written, so it is never published upward; only the rollup is. After a failover the Node knows *"camera 7 has footage for these ranges, on Server A's storage"* and nothing finer until Server A returns. Playback of old footage is coarse, not lost

---

## The zombie writer

The correctness core, and here the story is sharper than the usual telling, because **both writers are the same Node.**

Node N runs on Server A, writing camera 7's archive. Server A is partitioned — not dead, still reaching its cameras and its disks. Nomad concludes N is lost and starts N′ on Server B. Both are Node 3. Both hold N's configuration. Both believe they own camera 7.

**Nothing can tell dead from partitioned from paused.** That is not a gap to close with a better heartbeat; it is the fundamental limitation, and the design must be correct without resolving it.

### Why this cannot be solved by agreement

Some systems tolerate two nodes briefly believing they own the same object, because the data model reconciles — last-write-wins, CRDTs, quorum reads. That option is not available here:

> **Two writers to one video stream cannot be merged. There is no reconciliation function for footage.**

Which is why the epoch has to be a fencing token from a **single issuer**, rather than a value each Node computes for itself from what it believes about the cluster.

### Why a lock is not enough

Kleppmann's argument applies directly: a lock service cannot prevent a client whose lease expired during a pause from making unsafe changes, because it has no visibility into what the client does. The fix is a **fencing token**, and the essential part is *where it is checked* — **the resource must reject the stale token**, not the lock service.

### The VMS version, which is unusually clean

Make the epoch part of the archive's identity.

```
archive/node-3/epoch-000005/cam-7/seg-00042.mkv   <- the old instance
archive/node-3/epoch-000006/cam-7/seg-00000.mkv   <- the live one
```

The old instance cannot corrupt the new one's segments because **it cannot name them.** It writes valid files into a path the index no longer references, and retention deletes them.

> **You cannot stop a zombie from writing. You can only make its writes harmless.**

This is also the justification for the rule М10 introduced without one: **on restart, never resume the previous segment — open a new one.**

### Clocks

Lease expiry must not depend on wall-clock time; Redlock's flaw was exactly this, and system clocks jump under NTP correction.

- The holder measures its lease with a **monotonic** clock, never `gettimeofday`
- The holder **stops writing at TTL − margin**; a replacement **starts at TTL + margin**
- Safety depends on the two margins and on relative clock *rates*, never on two servers agreeing what time it is

The holder stopping is a purely local decision requiring no coordination, which is precisely why it is the part that can be trusted.

### Where the token actually comes from

The epoch has to come from **one issuer**, and the obvious candidates are worth walking, because the wrong one looks right.

**Not a Nomad variable lock.** Nomad has a lock primitive — acquire, renew, release, TTL between ten seconds and twenty-four hours — and it is exactly what a student will reach for. But **the lock ID is an opaque UUID: there is no monotonically increasing index.** That is precisely the lock Kleppmann's argument is about. It would reintroduce the zombie writer while appearing to have solved it, which makes it the most instructive wrong answer in the module.

**A Nomad Variable with check-and-set.** The Variables API takes a `cas` parameter compared against the variable's `ModifyIndex` and returns **409** on conflict:

```
GET  var domain/epoch        → { value: 41, ModifyIndex: 8123 }
PUT  var domain/epoch {42}  cas=8123
       200 → nobody else wrote      409 → re-read and retry
```

Atomic, single-issuer, monotonic. Simpler still: `ModifyIndex` is itself raft-assigned and monotonic, so a write of anything yields a usable epoch — fencing needs *increase*, not density.

**Why this beats a sequence in a database.** A sequence works right up until the database is restored from a backup, at which point it reissues numbers already written into archive paths — silent corruption produced by the recovery procedure itself. This is a large part of why the domain ended up with no database at all. Nomad's raft is replicated to every server; you cannot lose the counter without losing the cluster, and if the cluster is gone there are no allocations to fail over. **And it adds no coupling**: failover already requires Nomad, because Nomad is what reschedules the allocation.

Which generalises into the rule the whole module stores things by — **three stores, chosen by shape rather than by habit:**

| | Holds | Shape | Why not one of the others |
|---|---|---|---|
| **Postgres**, per Node | configuration, archive index, events | large, frequent, **queried** | the only one of the three that can answer a question |
| **Nomad Variables**, per Node and per domain | identity, the epoch, the directory list | small, rare, **must be consistent** | raft is memory-resident and replicated to every server, so it must stay small |
| **Object storage**, per domain | each Node's published configuration | large, rare, **never queried** | it is a blob nobody but its author parses, and durability is the whole requirement |

> **Small and consistent goes in the scheduler's store. Large and queryable goes in a database. Large and opaque goes in an object store.** The mistake this module started out making was assuming the third case needed the second.

---
## Lessons

*Four lessons. Several Nodes, scheduled — and the hard part is the data, not the scheduling.*

### Lesson 25 — When one box isn't enough

- What actually forces a second server: camera count, storage throughput, retention, availability
- **Why an orchestrator is the wrong answer for a single appliance.** Students should leave able to argue this, not assert it. On one box the scheduler has nothing to schedule — "place N workers" is a systemd template unit. Nomad's production guidance suggests 4–8+ cores and 16–32 GB+ for *servers* and says nothing about single-node deployments. And HashiCorp publishes a support note on orphaned Podman containers after an agent restart, which is a poor trade for no scheduling benefit
- Nomad's model: **servers** accept jobs and place work, **clients** register and execute it; raft per region, three or five servers
- Build a cluster: three servers, two clients
- **Break container-per-camera on purpose.** Run [`shard-memory-probe.py`](../М10_NodeVMS/reference/shard-memory-probe.py), measure the per-process baseline against the per-pipeline increment, and derive the shard size. Teach **proportional set size (PSS)** versus **resident set size (RSS)** — summing RSS across processes double-counts every shared library page, because it counts each shared page once per process

**Deliverable:** a working cluster, a measured shard size, and a written justification for why this deployment needed one.

---

### Lesson 26 — The Node as an allocation

- Jobspec structure: `job` → `group` → `task`, written in HCL
- **The Podman task driver** — the same images and runtime as Lesson 19. Translating a Quadlet unit into a Nomad task is a mapping, not a rewrite
- The other drivers and why a VMS cares: **`exec2`** for a native process needing device access, `virt` for a VM. Kubernetes cannot do this at all
- **`exec2` is not built into Nomad**, which is easy to get wrong: it is a separate official plugin downloaded onto each client host into the configured plugin directory, and it requires Linux with **Landlock LSM and cgroups v2**. Beta in 1.8.0, GA in 1.9.0. On an appliance that is one more thing the image must carry and the OS must support — a real constraint on М9's base distribution, not a footnote
- **Node identity: where it comes from, and where it must not.** The Node must be the same Node after it moves. Nomad's *allocation index* looks like the answer and has had documented uniqueness bugs — two simultaneously-running allocations sharing an index, accepted and later fixed. Fine for a metrics label; **never for something archive correctness depends on**
- **Nomad Variables are the right mechanism** — an encrypted, namespaced, ACL'd key-value store the scheduler delivers to a task. A Node reads *which Node am I, where is the directory, what is my epoch* from there. It is exactly what Variables are for, and it is why identity survives rescheduling without living on any disk
- **And why configuration does *not* go there.** Variables cap at **64 KiB per entry** — originally 16 KiB, raised since, and capped at all because, in HashiCorp's own words, the limit exists *"to reduce the potential performance impact of Variables on our raft store."* That is the maintainers stating this module's own reason: the raft store is memory-resident and replicated to every server, so it is the wrong place for anything that grows. A thousand cameras of settings do not fit, and a key-value store cannot answer *which cameras have retention over 30 days* anyway
- **What does fit is the pointer.** A Node's Variable holds its identity, its camera ids, and *where its configuration object is and at which revision* — hundreds of bytes, not megabytes. Lesson 29 turns that into the whole directory
- Storage reality: recordings stay local. **Do not put video bulk on replicated storage**
- Placement constraints: cameras are not uniformly reachable from every server

**Deliverable:** М10's Node running as a Nomad job with the behaviour it had under Quadlet — and a Node identity that survives being rescheduled.

---

### Lesson 27 — Making a Node's state outlive its server

The lesson the failover demo depends on, and the one most courses skip.

- **What must travel and what must not**, from the table above: configuration travels, footage stays, the index is rebuilt, events are expendable
- **2a — shared storage, and why it is a trap here.** A Container Storage Interface (CSI) volume looks like the grown-up answer: exclusive attachment even fences for you. But Nomad cannot detach a volume from a dead client, so the allocation will not place and a human has to intervene at the storage provider. Students should read the open issue rather than take this on trust
- **2b — the directory as an off-box backup**, which the course builds

#### The rehydration sequence

Walk it explicitly, because "it pulls its configuration back" hides every interesting decision:

```
Server A dies
  └─ Nomad reschedules Node 3's allocation → Server B
       1. empty Postgres; migrations run
       2. read its own Nomad Variable — "I am Node 3; my configuration
          is object node-3/rev-812, and these are my camera ids"
       3. fetch that object from the domain's object store
       4. restore it; check the revision against the Variable
       5. request a new epoch
       6. begin recording into epoch-N+1
```

- **Step 2 is why identity cannot live on disk.** The disk is on the dead server
- **Step 4 is where the RPO becomes visible.** The revision that comes back may be behind the one the operator last saw acknowledged
- **Steps 2 and 3 are why the domain must be reachable to fail over**, even though nothing in it is needed to *run*. Note they are two different things to reach — the scheduler's own store and an object store — and neither is a database somebody installed for this

#### The mechanism, and what not to build

**Not Postgres logical replication.** There is nothing at the domain to replicate *into*. A Node writes its configuration to the object store itself and then updates its own Variable to name the new revision — in that order, so a Variable never points at an object that is not there. The report channel from Lesson 29 carries status, not bulk.

- **The acknowledgement rule**, from the section above: acknowledge on local commit, and show *saved · not yet replicated* until the directory confirms. Never acknowledge a write whose durability you cannot vouch for, and never block the write on it either
- **A Node the directory has never seen** comes up *unconfigured*, and does not invent anything
- Rebuilding the archive index by scanning segments, and how long that takes at scale
- What the console must show while a Node's old footage is unreachable

**Deliverable:** two proofs. First, kill a Node and bring it back on another server with its configuration intact. Then **measure the RPO**: edit a camera, kill the Node in the window before it publishes, and show exactly what the operator was told and what actually survived — then reduce the window and show the number move.

---

### Lesson 28 — Failover, and the two instances of one Node

> **The two numbers this lesson has to export.** `node_failover_seconds` — power pulled to recording resumed — is the product's **RTO**, and it is meaningless as an average: report the worst case, because the customer's question is *how long could my site be dark*. And `node_epoch_conflicts` counts how often a stale instance was fenced at the archive; on a healthy system it is zero forever, which makes it exactly the kind of counter people forget to alarm on. **A metric that is always zero is worth more than one that is always noisy** — the day it moves, something the design said was impossible has happened.

- **Restart versus reschedule.** Restart retries on the same server; reschedule places on a different one. Service jobs default to unlimited attempts
- **The `disconnect` block, and why its default is wrong for a VMS.** By default a client missing heartbeats has its allocations marked lost and replaced *while the client keeps running its tasks* — so a partitioned server keeps recording while a replacement starts elsewhere. `lost_after`, `replace`, `stop_on_client_after`, and the four `reconcile` strategies. The exercise is the argument: for a recorder, is two servers recording the same camera for a minute better or worse than neither?
- **Fencing**, from the section above: the epoch in the archive path, monotonic clocks, and the two margins
- **Where the epoch comes from, and the wrong answer first.** Have students reach for Nomad's variable lock, then read the API and find the lock ID is an opaque UUID with no monotonic index — the exact lock Kleppmann warns about. Then build the right one: a Nomad Variable with `cas`, which is atomic, monotonic and survives losing the server that issued the last epoch
- **Planned failover.** Draining a client before an OS update, and returning it to service — the same mechanism, with a human choosing the moment. This is where М9's two update planes meet the scheduler
- What does not fail over: the footage

**Deliverable:** pull the power on a server; report how long until recording resumed and how many seconds were lost. Then restore it, let the old instance wake up, and prove the archive is intact and its output orphaned.

---
## Verification plan

**Track 1 — verified in the authoring sandbox.** More of this module than its subject matter suggests:

- **Fencing is fully testable with a filesystem and no cameras at all.** `kill -STOP`, restart the Node elsewhere, `kill -CONT`, assert on the directory tree. The correctness property has nothing to do with video
- The lease state machine, the epoch issuer's check-and-set behaviour, and revision handling
- The rehydration sequence against a fake directory, in the style of Lessons 11–15

**Track 2 — needs the real bench.** All of the scheduling: cluster formation, `nomad job validate`, Nomad Pack rendering, CSI attach and detach, and the power-pull exercise. Plus anything with real GStreamer and real cameras. Each lesson carries an explicit *expected output* block so a deviation is recognisable rather than mysterious.

---

## Open questions

1. **Is 2a ever right?** The course builds 2b, and the CSI detach problem means 2a cannot fail over unattended — so 2a is only defensible where an operator is on call. Whether any VMS deployment meets that description is a product question, not a technical one.
2. **Can a task write Variables under workload identity, and can an ACL policy scope it to that Node's own prefix?** Load-bearing rather than incidental: Node identity and the epoch both live in Variables, and М12's whole one-writer-per-key property rests on Node 3 being unable to write `nodes/node-4`. The Variables documentation does not settle it. **Check before building.**
3. **How long should a Node wait before concluding its old instance is gone?** Lesson 28 makes students pick the TTL and the margin either side of it; the product must pick them too, trading recovery time against the length of the window in which two instances exist.

**Resolved while designing the module:**

- ~~How is Node identity issued?~~ — a **Nomad Variable**, which is what Variables are for. Never the allocation index, which has had uniqueness bugs
- ~~Is shared storage the better product answer?~~ — no, and for a reason worth checking rather than assuming: Nomad cannot detach a CSI volume from a dead client, so 2a needs a human before it can fail over

---

## Sources

- [Nomad architecture](https://developer.hashicorp.com/nomad/docs/architecture) — servers and clients, raft, regions
- [Nomad task drivers](https://developer.hashicorp.com/nomad/plugins/drivers) — Podman, `exec2`, `virt`
- [Nomad `disconnect` block](https://developer.hashicorp.com/nomad/docs/job-specification/disconnect) — `lost_after`, `replace`, `stop_on_client_after`, and the four `reconcile` strategies
- [Nomad rescheduling](https://developer.hashicorp.com/nomad/docs/job-declare/failure/reschedule) — restart versus reschedule
- [Nomad production requirements](https://developer.hashicorp.com/nomad/docs/deploy/production/requirements) — server sizing, and the absence of single-node guidance
- [`NOMAD_ALLOC_INDEX` uniqueness bug](https://github.com/hashicorp/nomad/issues/10727) — two simultaneously-running allocations sharing an index; accepted and later fixed. Also [#4264](https://github.com/hashicorp/nomad/issues/4264) and [#11628](https://github.com/hashicorp/nomad/issues/11628) on consistency
- [Nomad CSI volumes do not recover from client failure without human intervention](https://github.com/hashicorp/nomad/issues/12118) — open; the volume stays attached to the dead node and must be detached manually
- [Nomad Variables HTTP API](https://developer.hashicorp.com/nomad/api-docs/variables/variables) — the `cas` parameter compared against `ModifyIndex`, 409 on conflict, and the 64 KiB item limit
- [Nomad Variable Locks](https://developer.hashicorp.com/nomad/api-docs/variables/locks) — acquire/renew/release with a TTL, and an **opaque lock ID rather than a fencing token**
- [Nomad Pack](https://developer.hashicorp.com/nomad/tools/nomad-pack) · [Nomad LICENSE](https://raw.githubusercontent.com/hashicorp/nomad/main/LICENSE)
- [How to do distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html) — fencing tokens, and why lease expiry must not depend on wall-clock time
- [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md) · [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md)

*Written 5 September 2026. Split from the combined DomainVMS module on 7 September 2026.*
