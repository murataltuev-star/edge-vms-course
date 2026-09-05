# М11_DomainVMS — Module Design

**A camera survives the death of the box that was recording it.**

М10's loop converged because nothing in it could disagree with itself: desired state in Postgres, actual state in a dictionary, both inside one process. Add a second node and three new things become possible, all of them bad.

1. The controller and a worker can **disagree about who owns a camera**.
2. A worker can be **alive, unreachable, and still writing**.
3. A change can be applied on one node and, silently and indefinitely, not on another.

This module is those three problems. It is the hardest correctness content in the course, and the only module where getting it wrong corrupts customer data rather than merely stopping a service.

> **Scope note.** [`COURSE-PLAN.md`](../COURSE-PLAN.md) originally gave М11 the whole of layer 4. М10 took the single-node half — the loop is much easier to see when both ends are in one terminal. What remains here is everything that only exists once there is more than one node. The write API is built in this module and is **deliberately unauthenticated**; М12 replaces it, the same way М10's hand-provisioned database password is replaced.

---

## The thesis

Two schedulers, and knowing which one owns what.

| | Places | Unit | Changes when | Must survive |
|---|---|---|---|---|
| **Orchestrator** (Nomad) | Workers onto nodes | "a recorder with capacity for 50 cameras" | Capacity changes | — |
| **Domain controller** | Cameras onto workers | "camera 812 records to archive 3" | An operator changes configuration | The orchestrator being down |

The second row is the module. [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) argued that camera lifecycle must never require a healthy control plane; this is where that argument gets built and tested.

### The demo it is built backwards from

Two hundred cameras across four workers. Then:

```
kill -STOP <worker-2>       # alive, holding its file handles, will come back
                            # exactly what a GC pause or a hung disk looks like
```

Worker 2's fifty cameras reappear elsewhere within the lease TTL. Then:

```
kill -CONT <worker-2>       # the zombie wakes up and tries to keep writing
```

**The archive is intact, and the student can prove it.** That last clause is the whole module — everything else is in service of it.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Scheduling | **Two levels, strictly separated** | The orchestrator never learns what a camera is. Adding a camera is a database write, not a deployment. |
| Convergence token | **Monotonic revision**, not token equality | Ordering expresses *distance*; equality only expresses *difference*. See below. |
| Worker config | **Opaque to the controller** | The controller stores, versions and delivers it, and never parses it. This is what allows a Go controller and a C++ worker, and what lets a new detector type ship without touching controller code. |
| Placement | **Stable by default; rebalance explicit and budgeted** | Adding a worker must move *nothing*. A load-balancing heuristic that fires on membership change turns one worker joining into two hundred gaps in the archive. |
| Fencing | **At the archive, not at the controller** | You cannot stop a zombie from writing. You can make its writes land where nobody reads. |
| Status model | **Positions and reasons kept apart** | Kubernetes shipped a phase enum and then wrote down, at length, why it was a mistake. |
| Transport | **Server-streaming watch, client-streaming report** | The worker never has to be addressable. Sites sit behind NAT; a controller that must dial its workers does not survive contact with a customer network. |
| Write API | **Built here, unauthenticated, marked temporary** | Follows the course's existing discipline: the stand-in is named as a stand-in at the point it appears, not discovered later. |
| Database placement | **One Postgres per domain; hosts cache, never replicate** | There is exactly one writer, so the synchronisation problem never arises. See [`where-the-database-lives.md`](where-the-database-lives.md). |
| Domain boundary | **The largest set of nodes sharing a reliable network** | Span a link you do not trust and configuration depends on it. This is what makes the scope progression physical. |

---

## Prerequisites

- **М10 entire.** This module rewrites its AppHost into a worker that takes instruction instead of deciding for itself, and Lesson 33 is the migration.
- **Lesson 21** — the Nomad job. Workers are allocations; cameras are not.
- **Lessons 5–6** — signals. `kill -STOP` is the module's most important teaching device.
- **М9's process-model record** — capacity, shard sizing, and why the orchestrator must not own camera lifecycle.

---

## Why ordering beats equality

The convergence token could be an opaque value compared for equality — "was this exact configuration applied?" — or an ordered revision — "has actual reached this version?" They are not equivalent, and the choice propagates into every layer above.

Ordering wins on three counts:

1. **It expresses distance, not just difference.** "Diverged" is an alert you learn to ignore. "Diverged by four revisions for forty minutes" is an incident. Equality cannot tell the two apart.
2. **It permits skip-ahead.** A worker offline across revisions 7, 8 and 9 converges straight to 9. It does not replay the intermediate states, because there is nothing in an ordered token that says it must. Edge workers go offline constantly, so this is not an optimisation.
3. **It survives replay and reordering.** A late report carrying a lower revision is simply ignored. Under equality it is ambiguous — is this stale, or did someone revert?

**The cost, stated honestly:** you lose the ability to prove that one *precise* configuration was applied at one moment. If that has to be auditable, it belongs in an audit log, not in the convergence token. Conflating the two produces a token that does neither job well.

---

## Placement that does not churn

Placement is not scheduling. The orchestrator answers "where does this workload run"; the controller answers "which worker owns this camera", against capacity it did not choose and constraints the orchestrator knows nothing about.

**Capacity comes from measurement.** М10 shipped `shard-memory-probe.py` precisely so this number is observed rather than guessed, and it differs per worker class: a recorder holds far more cameras than a detector worker.

**Constraints come from physics.** A camera on an isolated VLAN is reachable from one site's nodes and no others. A detector needs a node with a GPU. The controller does not understand networks or hardware — it matches labels.

### The rule

> **Only move a camera when you must.** Three triggers, and nothing else: the camera is new, its worker is gone, or an operator asked for a rebalance.

Moving a camera costs a pipeline restart and a discontinuity in the archive. A tidy-looking "keep the workers evenly loaded" heuristic fires on every membership change, and one worker joining a cluster becomes two hundred small gaps in customer footage. The property test in Lesson 31 is therefore blunt: **add a worker, assert that no existing camera moved.**

### Why not consistent hashing

It is the reflexive answer — it minimises movement under membership change — and it is wrong here for three reasons:

- **Cameras are not uniform.** A 4K camera at 8 Mbps and a 720p camera at 1 Mbps are one hash slot each and eight times apart in cost.
- **Constraints break the ring.** A camera reachable only from site A cannot be allowed to hash onto site B, and every exception you carve out erodes the property you adopted hashing for.
- **It is not inspectable.** At 3am the question is "why is camera 812 on worker 3", and the answer should be a row you can select, with the reason and the timestamp it was decided — not a hash you have to recompute in your head.

**Store the assignment; do not derive it.** The controller writes a placement row with its own revision, and that row is the answer.

Rebalance, when it is genuinely wanted, is a separate explicit operation: **budgeted** (at most N moves per minute), observable, and interruptible.

---

## The zombie writer

The correctness core of the module, and the one place where a mistake destroys recordings rather than interrupting them.

### The situation

Worker A holds camera 7 under lease epoch 5 and is writing segments. A stops responding.

**The controller cannot tell dead from partitioned from paused.** This is not a gap in the design to be closed by a better heartbeat; it is the fundamental limitation, and the design has to be correct without resolving it. So the controller does the only safe thing: it waits for the lease to expire and grants camera 7 to worker B at epoch 6.

Then A wakes up. Its process never died. Its file handles are still open. As far as A knows, it owns camera 7.

### Why a lock is not enough

Kleppmann's argument about distributed locking applies directly: a lock service alone cannot prevent a client whose lease expired during a pause from making unsafe changes, because the lock service has no visibility into what the client does with the lock. The fix is a **fencing token** — a monotonically increasing number issued on acquisition — and the essential part is *where it is checked*: **the resource must reject the stale token**, not the lock service.

### The VMS version, which is unusually clean

Make the epoch part of the archive's identity.

```
archive/cam-7/epoch-000005/seg-00042.mkv     <- A, the zombie
archive/cam-7/epoch-000006/seg-00000.mkv     <- B, the live writer
```

A cannot corrupt B's segments because **A cannot name them.** It writes valid files into a directory the index no longer references, and retention eventually deletes them.

> **You cannot stop a zombie from writing. You can only make its writes harmless.**

This is also the justification for the rule М10 introduced without one: **on regaining a lease, never resume — always open a new segment.** Resuming means writing into the previous epoch's namespace, which is exactly the collision the epoch exists to prevent.

### Clocks

The other half of Kleppmann's argument is that lease expiry must not depend on wall-clock time — Redlock's flaw was exactly this, and system clocks take discontinuous jumps under NTP correction. Three rules:

- The holder measures its own lease with a **monotonic** clock. Never `gettimeofday`.
- The holder **stops writing at TTL − margin**. The controller **grants at TTL + margin**.
- Safety therefore depends on the two margins and on relative clock *rates*, never on two nodes agreeing what time it is.

The holder stopping is a purely local decision requiring no coordination — which is precisely why it is the part that can be trusted.

---

## Lessons

### Lesson 30 — Two schedulers, and the contract between them

- Why a second scheduler exists at all, and what the orchestrator must never learn
- Desired state published by the controller; actual state reported by workers; `observed_revision >= revision` as the only test of applied, unchanged from М10
- **Streams, not callbacks.** A server-streaming watch and a client-streaming report mean the worker is never required to be addressable — which is what makes this work behind a customer's NAT
- Resume tokens: a reconnecting worker says where it got to, and gets a delta rather than a full resync
- **Why ordering beats equality**, from the section above
- **Opaque config**, and the failure mode when a worker receives configuration it cannot parse — which is a reportable divergence, not a crash
- **Hosts cache; they do not replicate.** There is one database per domain and one writer. A worker persists its assignment, its config and its lease locally — SQLite is plenty — so a host rebooting while the controller is unreachable comes back recording rather than idle. This is not a second source of truth; it is a cache with an expiry, and Lesson 32 is about that expiry
- **Where the domain ends:** at the first network link you would not bet recording on. Nodes on one reliable network form one domain; anything past that is federation, not a bigger domain

**Deliverable:** the contract, and М10's AppHost rewritten as a worker that subscribes and reports instead of deciding for itself — including surviving a reboot with the controller switched off.

---

### Lesson 31 — Placement that doesn't churn

- Capacity per worker class, taken from М10's measurements
- Constraints as labels: site, reachability, hardware
- **The stability rule**, and the property test that enforces it — *adding a worker moves nothing*
- Why not consistent hashing
- Budgeted rebalance: a rate limit, a reason recorded per move, and the ability to stop it halfway
- Placement as a stored row with its own revision, not a function evaluated on demand

**Deliverable:** a placement function with property tests, including the one that fails loudly the first time somebody adds a tidy-looking rebalance.

---

### Lesson 32 — Leases, epochs, and the zombie writer

The module's correctness lesson. See *The zombie writer* above.

- Why dead, partitioned and paused are indistinguishable, and why the design must not care
- Lease structure: holder, epoch, TTL. The epoch increments on every ownership change and never decreases
- **Monotonic clocks and the two margins**
- **Fencing at the archive:** the epoch in the path, and what the zombie's output actually is afterwards
- `kill -STOP` and `kill -CONT` as the reproduction, because they produce a genuinely paused process rather than a simulated one
- **The other stale-state failure, which destroys data rather than interrupting it.** A worker cut off from the controller holds a cached desired state that ages. What is safe to do with it depends on the operation: keeping an existing recording running is safe indefinitely; starting something new is questionable; **deleting footage under a cached retention policy is not safe at all.** An operator raises retention from 7 days to 30 on Monday, a host loses contact on Tuesday, and on Wednesday it obediently deletes everything older than a week
- **The rule: destructive operations stop at the grace period; recording does not.** A host that cannot confirm its retention policy keeps footage and says so. Full disks are visible and recoverable; deleted footage is neither

**Deliverable:** STOP a worker, watch its cameras reassign, CONT it, and prove both that the archive is intact and that the zombie's segments are orphaned rather than interleaved. Then cut a worker off, expire its cache, and prove it kept recording and deleted nothing.

---

### Lesson 33 — Shadow mode: the controller that writes nothing

The course's own convention — the stand-in before the real thing — applied at the top layer. `camera_sim.py` before the pipeline; a fake actuator before GStreamer; and now a controller that observes before it commands.

- The controller computes what desired state *would* be, observes what is actually running, emits a divergence report, and **changes nothing**. It can be switched off without consequence
- **The divergence taxonomy**, because "it didn't work" is not a diagnosis:

| Kind | Meaning | Is it a fault? |
|---|---|---|
| **Lagging** | Behind, within the grace period | No — normal and transient |
| **Stalled** | Behind past grace, and progress is not advancing | **Yes** — this is the real "it didn't take effect" |
| **Orphaned** | Desired state exists, nobody is reporting | Yes — dead worker, or never placed |
| **Unmanaged** | Something is running with no desired state | In shadow mode, **a measurement, not a fault** |
| **Conflict** | Two reporters claim one object | Always — a fencing failure or a double assignment |
| **Stale epoch** | A report arrived under a superseded epoch | The fencing rule catching a writer that should have stopped |
| **Unintelligible** | Reporting, but the config type is unknown | A schema gap |

- **The one number that matters: `unmanaged == 0`.** Something running that the model does not describe is a gap in the model. Driving that count to zero *is* the design work, and it is how М10's self-directed AppHost migrates to controller-directed without a flag day
- **Slow versus stuck:** how long an object has been diverged, and whether its progress counter moved. Two hundred milliseconds is noise; forty minutes with a static counter is an incident

**Deliverable:** a divergence report against the student's own running system, and a written exit criterion for switching the controller into write mode.

---

### Lesson 34 — The API, and what it refuses

- The read view: desired and observed joined in one query, **grouped by failure domain**, so that a dead node reads as one cause rather than fifty faults
- **Positions and reasons.** `phase` says where an object is in its lifecycle; conditions say why it cannot get further — licence satisfied, storage writable, device reachable. Kubernetes shipped the phase enum and then documented why it was wrong: enums are not extensible, every addition is a breaking change, and clients inevitably switch on them
- Write API: camera CRUD, with **idempotency keys** so that a retried request does not create a second camera
- **What the API refuses.** Placement is not a field a client may set. The operator assigns cameras to *sites*, never to workers or nodes — М10's rule, now enforced at the boundary
- **Detectors.** Attaching a detector creates another object, of another worker class, with its own opaque config. The controller does not change. Which means **where inference runs — on the appliance, at the camera, or in the cloud — is a deployment question answered by worker class and placement constraints, not a schema question.** This resolves an open question carried since the course plan was written
- The API is unauthenticated, and the lesson says so in as many words

**Deliverable:** the console showing two hundred cameras across four nodes; kill one node; one cause displayed.

---

## Verification plan

**The most verifiable module since М8**, which is a pleasant surprise for the one with the hardest content. Both of the module's difficult ideas are testable in the authoring sandbox:

**Track 1 — verified here.**

- **Placement** is a pure function. Property tests are the natural fit: adding a worker moves nothing; every camera lands on exactly one worker; no constraint is violated; a removed worker's cameras all move and no others do
- **Fencing is fully testable with a filesystem and no cameras at all.** `kill -STOP`, reassign, `kill -CONT`, and assert on the resulting directory tree. Segments can be written by `filesink` or by a script; the correctness property has nothing to do with video
- The lease state machine, the divergence taxonomy, resume tokens, and idempotency are all ordinary logic
- Streaming behaviour tested against a fake worker, in the style of Lessons 11–15

**Track 2 — needs the real bench.** Only what involves real GStreamer, real cameras and real Nomad: end-to-end reassignment timing, and the console against a live system.

---

## Open questions

1. **Rebalance trigger.** Operator-initiated only, or scheduled during a maintenance window? The module currently assumes the former.
2. **How much retention policy is domain design rather than infrastructure?** Schedules, per-camera overrides and legal hold are product decisions that may deserve their own lessons.
3. **Does a domain need database HA at all, or is backup-and-restore honest?** [`where-the-database-lives.md`](where-the-database-lives.md) recommends single-node by default and witness-based HA on request; whether the product ships the option is a commercial decision.

**Resolved by [`where-the-database-lives.md`](where-the-database-lives.md):**

- ~~What happens when the controller is down~~ — workers keep recording from cached assignments; configuration, reassignment and deletion stop
- ~~One controller per site or per fleet~~ — one per domain, and a domain is one reliable network
- ~~Does the archive index share Postgres with configuration~~ — no. The host that wrote the footage owns its index; the domain holds a rollup only

---

## Sources

- [How to do distributed locking](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html) — Martin Kleppmann on fencing tokens: a lock service cannot prevent a paused client from writing, the resource must reject the stale token, and lease expiry must not depend on wall-clock time
- [Eliminate Phase and simplify Conditions](https://github.com/kubernetes/kubernetes/issues/7856) — Kubernetes on why phase enums were a mistake: not extensible, every addition breaking, clients switch on them
- [Kubernetes API conventions](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api-conventions.md) — the conditions model in its mature form
- [Pod conditions](https://kubernetes.io/docs/concepts/workloads/pods/pod-condition/) — conditions as reasons rather than positions, in practice
- [`where-the-database-lives.md`](where-the-database-lives.md) — one database per domain, caching versus replication, and the rule that destructive operations must never run from a stale cache
- [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) — the two-level scheduling argument this module implements

*Written 4 September 2026.*
