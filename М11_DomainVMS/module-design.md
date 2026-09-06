# М11_DomainVMS — Module Design

**Many nodes, and a camera that survives the death of the server recording it.**

М10 built one Node: a database holding what it should be, and a loop making it so. This module runs several, makes each one outlive the server it happens to be on, and then adds the small layer that has to sit above them.

The organising decision, taken up front because everything depends on it: **a Node owns its own configuration.** Nomad moves the Node; the cameras go with it; nothing rewrites who owns what. That choice is argued below against the two obvious alternatives, and it is what makes failover teachable in Part A instead of deferred.

> **Scope note.** These lessons were briefly М9's Part B and then briefly М10's Part B before landing here. The last move happened because a cluster and the layer above it are one arc, and splitting them meant teaching the two-level idea twice. Nomad's cross-site federation went further still, to [М12](../М12_FederatedVMS/module-design.md), where many networks actually begin. The write API is built here and is **deliberately unauthenticated**; М12 replaces it, the same way М10's hand-provisioned database password is replaced.

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

## Three ways to own a camera

Every design in this space is a different answer to *who owns camera 7*. Three are worth building; one is the module's choice; the discarded two fail late enough to be worth walking into.

### Candidate 1 — the cameras are Nomad allocations

You already have a scheduler that places things, holds state and survives node failure. Rejected on four counts:

- **Cardinality and churn.** An allocation is one of tens per node, changing when engineers deploy. A camera is one of thousands per site, changing when an operator clicks
- **State in the wrong place.** A thousand cameras in Nomad's raft makes every operator edit consensus traffic replicated to every server
- **Availability coupling.** Adding a camera would require healthy Nomad servers. A VMS must accept configuration and keep recording through a control-plane outage
- **No ownership primitive.** Allocations are *placed* and nothing contests them, so Nomad has never needed a lease or an epoch

### Candidate 2 — the Node owns its cameras · **the module's choice**

Each Node holds its own configuration in its own database, is the only writer of those rows, and replicates them one way upward. The camera→Node mapping is written once, when the camera is created, and never rewritten — **because the thing that moves during failover is the Node, not the camera.**

It inherits М10 exactly: what М10 built on one box *is* a Node. It keeps configuration next to the software that uses it. And it is strictly more available than a central configuration store — an operator who can reach Node N can edit camera 7 while the domain layer is unreachable.

**Its residue, named honestly.** Three things a Node cannot know about itself, which is what Part B builds:

| | Why a Node cannot answer it |
|---|---|
| **Lookup** — where is camera 7? | Asking every Node cannot distinguish *deleted* from *unreachable* |
| **Creation** — which Node gets a new camera? | Capacity and reachability across Nodes is domain knowledge by definition |
| **Rebalance** — move camera 7 from N to M | Two single-writer databases, no coordinator, no transaction |

That is a **directory**, not a configuration store. Small, and it may be down without recording or configuration stopping.

### Candidate 3 — do not store ownership, derive it

`owner = f(camera_id, membership)`. Every node computes the same answer; nothing is written. This is how the Dynamo family works, and pushed further it is coherent: take membership from Nomad's raft, let the epoch be the membership generation, have each node self-fence on staleness.

It breaks one level in. **"Everyone knows who owns what" is a claim about agreement**, and since ownership is derived, every disagreement moves into the input:

- Node A is partitioned but alive and still reaching its cameras
- B and C compute membership `{B,C}` → camera 7 is B's → B records
- A computes `{A,…}` → camera 7 is still A's → A records
- **Both write** — split-brain reached by two nodes each applying the same function correctly

Three further costs: ownership **churns by construction** (any membership change recomputes all of it, violating Lesson 30's *only move a camera when you must*); **constraints cannot live in a pure function** (a camera on an isolated VLAN needs stored constraint data, which needs an owner, and the design collapses back); and it **re-couples camera lifecycle to cluster health.**

### The difference that decides it

Dynamo-style systems tolerate two nodes briefly believing they own a key **because the data model reconciles.**

> **Two writers to one video stream cannot be merged. There is no reconciliation function for footage.**

Which is why the epoch must be a **fencing token from a single issuer** rather than a number each node computes — and why Candidate 3 is not available to a VMS however elegant it looks.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Camera ownership | **The Node owns it, permanently** | Failover moves the Node, so nothing rewrites ownership. Candidate 2 above. |
| Node identity | **Stable, carried in a Nomad Variable — never the allocation index** | Variables are built for exactly this and survive rescheduling. The allocation index has had documented uniqueness bugs: fine for a label, never for correctness. |
| Configuration authority | **The Node, replicating one way upward** | Configuration stays next to the software using it, and survives the domain layer being down. |
| Domain layer | **A directory, not a configuration store** | Lookup, creation and rebalance only. It may be unavailable without recording stopping. |
| Fencing | **At the archive, not at the controller** | You cannot stop a zombie from writing. You can make its writes land where nobody reads. |
| Orchestrator | **Nomad** | Non-container workloads, Podman kept as the runtime, far smaller operational surface. See [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md). |
| Single-server deployments | **No orchestrator at all** | М9's Quadlet stack is better on one box, and Lesson 25 makes students argue that rather than assert it. |
| Convergence token | **Monotonic revision**, not token equality | Ordering expresses *distance*; equality only *difference*. See below. |
| Status model | **Positions and reasons kept apart** | Kubernetes shipped a phase enum and then documented why it was a mistake. |

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

The correctness core, and under Candidate 2 the story is sharper than the usual telling, because **both writers are the same Node.**

Node N runs on Server A, writing camera 7's archive. Server A is partitioned — not dead, still reaching its cameras and its disks. Nomad concludes N is lost and starts N′ on Server B. Both are Node 3. Both hold N's configuration. Both believe they own camera 7.

**Nothing can tell dead from partitioned from paused.** That is not a gap to close with a better heartbeat; it is the fundamental limitation, and the design must be correct without resolving it.

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

---

## Why ordering beats equality

Configuration replicates one way from each Node upward, and the domain must be able to say how far behind it is. The token could be an opaque value compared for equality, or an ordered revision. Ordering wins three ways:

1. **It expresses distance, not just difference.** "Diverged" is an alert you learn to ignore; "diverged by four revisions for forty minutes" is an incident
2. **It permits skip-ahead.** A subscriber offline across revisions 7, 8 and 9 converges straight to 9 without replaying. Edge links go down constantly, so this is not an optimisation
3. **It survives replay and reordering.** A late report carrying a lower revision is ignored rather than ambiguous

**The cost:** you lose proof that one *precise* configuration was applied at one moment. If that must be auditable it belongs in an audit log, not in the convergence token.

---

## Placement that does not churn

Under Candidate 2 placement happens **twice in a camera's life** — when it is created, and if an operator rebalances — and never in between. That makes the rule easy to state and easy to violate:

> **Only place a camera when you must.** Two triggers: the camera is new, or an operator asked for a rebalance. A dead server is *not* a trigger, because the Node moves and the camera goes with it.

**Capacity comes from measurement.** М10 shipped [`shard-memory-probe.py`](../М10_NodeVMS/reference/shard-memory-probe.py) precisely so this is observed rather than guessed. **Constraints come from physics** — a camera on an isolated VLAN is reachable from some Nodes and not others.

### Why not consistent hashing

The reflexive answer, and wrong here: cameras are **not uniform** (4K at 8 Mbps beside 720p at 1); **constraints break the ring**; and it is **not inspectable** — at 3am *"why is camera 812 on Node 3"* should be a row with a reason and a timestamp, not a hash to recompute.

**Store the placement; do not derive it.** Rebalance, when genuinely wanted, is explicit: **budgeted** at N moves per minute, observable, and interruptible.

---

## Part A — Nodes that outlive their servers

*Several Nodes, scheduled. The hard part is the data, not the scheduling.*

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
- The other drivers and why a VMS cares: `exec2` for a native process needing device access, `virt` for a VM. Kubernetes cannot do this at all
- **Node identity: where it comes from, and where it must not.** The Node must be the same Node after it moves. Nomad's *allocation index* looks like the answer and has had documented uniqueness bugs — two simultaneously-running allocations sharing an index, accepted and later fixed. Fine for a metrics label; **never for something archive correctness depends on**
- **Nomad Variables are the right mechanism** — an encrypted, namespaced, ACL'd key-value store the scheduler delivers to a task. A Node reads *which Node am I, where is the directory, what is my epoch* from there. It is exactly what Variables are for, and it is why identity survives rescheduling without living on any disk
- **And why configuration does *not* go there.** Variables cap at **16 KiB per entry, not configurable** — capped, in HashiCorp's own words, *"to reduce the potential performance impact of Variables on our raft store."* That is the maintainers stating this module's own objection to Candidate 1: the raft store is memory-resident and replicated to every server. A thousand cameras do not belong in it, and a key-value store cannot answer *which cameras have retention over 30 days* anyway
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
       2. read identity from a Nomad Variable — "I am Node 3, the directory is at X"
       3. ask the directory for Node 3's last published configuration
       4. restore it; check the revision it came back with
       5. request a new epoch
       6. begin recording into epoch-N+1
```

- **Step 2 is why identity cannot live on disk.** The disk is on the dead server
- **Step 4 is where the RPO becomes visible.** The revision that comes back may be behind the one the operator last saw acknowledged
- **Step 5 is why the directory must be reachable to fail over**, even though it is not needed to run

#### The mechanism, and what not to build

**Not Postgres logical replication.** The Node's report channel from Lesson 29 already streams upward; configuration revisions ride on it beside status. One mechanism, two uses, and no second piece of infrastructure to operate.

- **The acknowledgement rule**, from the section above: acknowledge on local commit, and show *saved · not yet replicated* until the directory confirms. Never acknowledge a write whose durability you cannot vouch for, and never block the write on it either
- **A Node the directory has never seen** comes up *unconfigured*, and does not invent anything
- Rebuilding the archive index by scanning segments, and how long that takes at scale
- What the console must show while a Node's old footage is unreachable

**Deliverable:** two proofs. First, kill a Node and bring it back on another server with its configuration intact. Then **measure the RPO**: edit a camera, kill the Node in the window before it publishes, and show exactly what the operator was told and what actually survived — then reduce the window and show the number move.

---

### Lesson 28 — Failover, and the two instances of one Node

- **Restart versus reschedule.** Restart retries on the same server; reschedule places on a different one. Service jobs default to unlimited attempts
- **The `disconnect` block, and why its default is wrong for a VMS.** By default a client missing heartbeats has its allocations marked lost and replaced *while the client keeps running its tasks* — so a partitioned server keeps recording while a replacement starts elsewhere. `lost_after`, `replace`, `stop_on_client_after`, and the four `reconcile` strategies. The exercise is the argument: for a recorder, is two servers recording the same camera for a minute better or worse than neither?
- **Fencing**, from the section above: the epoch in the archive path, monotonic clocks, and the two margins
- **Planned failover.** Draining a client before an OS update, and returning it to service — the same mechanism, with a human choosing the moment. This is where М9's two update planes meet the scheduler
- What does not fail over: the footage

**Deliverable:** pull the power on a server; report how long until recording resumed and how many seconds were lost. Then restore it, let the old instance wake up, and prove the archive is intact and its output orphaned.

---

## Part B — The domain above the Nodes

*The three things a Node cannot know about itself. Small, and allowed to be down.*

### Lesson 29 — What the domain knows that a Node cannot

- The directory: which Nodes exist, which cameras belong to which, and how far behind each Node's replica is
- **Why it is small, and why that matters.** It is not a configuration store — it may be unavailable while recording continues and while an operator edits a camera on its own Node
- The contract: **streams, not callbacks.** A server-streaming watch and a client-streaming report mean a Node is never required to be addressable, which is what makes this work behind a customer's NAT
- **Why ordering beats equality**, from the section above
- **Opaque config** — the domain stores and delivers what it does not parse, which is what lets a new worker class ship without touching it
- **Where the domain ends:** at the first network link you would not bet recording on

**Deliverable:** the directory, and a Node that registers, replicates upward and reports how far behind it is.

---

### Lesson 30 — Placement: where a new camera goes

- Capacity per Node from М10's measurements; constraints as labels
- **The stability rule** and its property test — *adding a Node moves nothing*
- Why not consistent hashing
- Rebalance as a two-writer move: budgeted, reasoned, interruptible, and why it needs the domain to coordinate it
- Placement as a stored row with its own revision

**Deliverable:** a placement function with property tests, including the one that fails the first time somebody adds a tidy-looking rebalance.

---

### Lesson 31 — Shadow mode: the domain that writes nothing

The course's own convention — the stand-in before the real thing — at the top layer.

- The domain computes what the directory *would* say, observes what Nodes report, emits a divergence report, and changes nothing

| Kind | Meaning | Fault? |
|---|---|---|
| **Lagging** | behind, within grace | No — normal |
| **Stalled** | behind past grace, progress static | **Yes** — the real "it didn't take effect" |
| **Orphaned** | a camera in the directory no Node claims | Yes |
| **Unmanaged** | a Node recording something the directory does not know about | In shadow mode, **a measurement, not a fault** |
| **Conflict** | two Nodes claim one camera | Always — a fencing or placement failure |
| **Stale epoch** | a report under a superseded epoch | The fencing rule catching a writer that should have stopped |

- **The one number: `unmanaged == 0`** — anything running the model does not describe is a gap in the model, and driving it to zero *is* the design work
- **Slow versus stuck:** how long diverged, and whether progress moved

**Deliverable:** a divergence report against the student's own cluster, and a written exit criterion for switching the domain into write mode.

---

### Lesson 32 — The API, and what it refuses

- The read view: the directory joined with what Nodes report, **grouped by failure domain**, so a dead server reads as one cause
- **Positions and reasons.** `phase` says where an object is; conditions say why it cannot get further. Kubernetes shipped the phase enum and then documented why it was wrong
- Write API: camera CRUD with **idempotency keys**, and **what it refuses** — a client may not set placement
- **Detectors.** Another worker class with its own opaque config; the domain does not change. So **where inference runs is a deployment question**, not a schema one
- The API is unauthenticated, and the lesson says so

**Deliverable:** the console showing two hundred cameras across four Nodes; kill a server; one cause displayed.

---

### Lesson 33 — Packaging, delivery, and the licence

- **Nomad Pack**: templating, variables and registries; per-site differences without per-site forks
- **The honest GitOps gap.** Fleet is pull-based — a site catches up by itself. Nomad Pack driven from CI is push-based; your pipeline must reach each region. For flaky edge links that is materially worse, and the module says so rather than glossing it. hawkBit in М12 restores it on the OS plane
- **Reading the licence you just built on.** Nomad Community Edition is under the **Business Source License (BUSL)**, a source-available licence whose grant converts to an open-source one after a Change Date. Licensor IBM; production use is granted provided the work is not offered to third parties on a hosted or *embedded* basis to compete with IBM's paid versions; the Change Date is four years per version, converting to MPL 2.0. Full analysis in [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md)
- Acceptance criteria for the module

**Deliverable:** one pack, three simulated sites, per-site differences — plus a written analysis of what breaks when a site is offline for a day.

---

## Verification plan

Split by part, and the split is clean.

**Track 1 — verified in the authoring sandbox.** Everything that is logic or a file:

- **Fencing is fully testable with a filesystem and no cameras at all.** `kill -STOP`, restart the Node elsewhere, `kill -CONT`, assert on the directory tree. The correctness property has nothing to do with video
- **Placement** is a pure function — property tests are the natural fit: adding a Node moves nothing; every camera lands on exactly one; no constraint violated
- The lease state machine, the divergence taxonomy, one-way replication and revision handling, resume tokens, idempotency
- Streaming behaviour against a fake Node, in the style of Lessons 11–15

**Track 2 — needs the real bench.** All of Part A's scheduling: cluster formation, `nomad job validate`, Nomad Pack rendering, CSI attach/detach, and the power-pull exercise. Plus anything with real GStreamer and real cameras. Part A lessons carry explicit *expected output* blocks so a deviation is recognisable rather than mysterious.

---

## Open questions

1. **Is 2a ever right?** The course builds 2b, and the CSI detach problem means 2a cannot fail over unattended — so 2a is only defensible where an operator is on call. Whether any VMS deployment meets that description is a product question, not a technical one.
2. **Rebalance trigger.** Operator-initiated only, or scheduled during a maintenance window? The module assumes the former.
3. **How much retention policy is domain design rather than infrastructure?** Schedules, per-camera overrides and legal hold may deserve their own lessons.
4. **Does the directory need high availability (HA)?** It may be down without recording stopping, which is the point — but failover cannot *complete* without a new epoch, so the token issuer is more load-bearing than the rest of it.

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
- [Configurable max entry size for Nomad Variables](https://github.com/hashicorp/nomad/issues/14763) — 16 KiB, not configurable, capped to limit the impact on a memory-resident raft store
- [Nomad Pack](https://developer.hashicorp.com/nomad/tools/nomad-pack) · [Nomad LICENSE](https://raw.githubusercontent.com/hashicorp/nomad/main/LICENSE)
- [How to do distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html) — fencing tokens, and why lease expiry must not depend on wall-clock time
- [Eliminate Phase and simplify Conditions](https://github.com/kubernetes/kubernetes/issues/7856) — why phase enums were a mistake
- [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md) · [`where-the-database-lives.md`](where-the-database-lives.md) · [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md)

*Written 5 September 2026.*
