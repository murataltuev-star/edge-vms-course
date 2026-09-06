# Where the Databases Live

**A decision record for М10_NodeVMS and М11_DomainVMS.** Companion to [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) and [`consul-and-openbao.md`](../М12_FederatedVMS/consul-and-openbao.md), written in answer to *"a domain exists when its Postgres exists — so is Postgres installed on every host, and how do they sync?"*, and then revised in answer to a second question that corrected it: *if the host needs an event store and an index anyway, why not Postgres locally too?*

It has now been revised a third time, and that revision **inverts the second verdict below.** The question that did it: *the Node is a Nomad allocation, not a server — its configuration does not change when Nomad moves it from one server to another, so why does anything need to write ownership at all?* That is right, and the design changed because of it. Each revision is left visible rather than quietly edited out, because the sequence is the lesson.

---

## Verdict

Four things. The second is the one this record got wrong twice before getting right.

1. **Two databases, one engine.** A *node database*, one per Node, and a *domain directory*, one per domain. Both Postgres; almost nothing else about them is alike.
2. **The Node owns its configuration and replicates one way upward.** It does not cache someone else's. A Node is a Nomad allocation with stable identity, so when a server dies the Node moves and **its cameras go with it** — nothing rewrites ownership, because ownership never changed.
3. **Destructive operations must never run from state whose authority is unreachable.** Recording continues; deletion does not.
4. **A domain is the largest set of nodes sharing a reliable network.** That is what decides where domain boundaries fall, and it makes the whole Edge → Node → Domain → Federation progression physical rather than arbitrary.

---

## A domain is its database

The question contained its own answer. A domain is defined by the desired state that describes it, so **one domain means one authoritative database**, and adding hosts to a domain does not add databases to it.

Putting Postgres on every host and synchronising would mean choosing one of:

- **Multi-master** — and for a *desired state* store, conflicting writes are precisely the thing that must not happen. "Camera 7 belongs to worker A" and "camera 7 belongs to worker B", merged, is the split-brain М11 Lesson 29 exists to prevent
- **A consensus system** — which is what you would end up building, and Postgres is not one
- **Primary with read replicas** — the sane version of the idea, and a legitimate option, but it is an availability choice rather than a way to make every host self-sufficient

None of these is needed, because hosts do not need the database.

---

## The Node owns; the domain observes

Two earlier drafts of this record had it the other way round — a domain database as the authority, hosts holding read-only caches of their slice. That works, and it is what most control planes do. It is also **strictly less available than it needs to be**, and the reason is worth stating precisely.

**A Node is a Nomad allocation, not a server.** Its identity is stable; Nomad decides which hardware runs it. So camera 7 belongs to Node 3 *permanently*, and a dead server is not an ownership change — it is a relocation. Nothing has to write "Node 3 now lives on Server B" into a configuration store, because no camera moved.

Once that is true, the case for a central configuration authority mostly evaporates:

| | Domain-authoritative (earlier drafts) | **Node-authoritative** (this design) |
|---|---|---|
| Who writes camera 7's config | the controller | **Node 3, the only writer** |
| Editing while the domain is unreachable | impossible | **works — reach the Node** |
| Ownership on failover | rewritten by the controller | **never rewritten** |
| What the domain holds | everything | a directory and a durable copy |

Both designs have exactly one writer per row, so neither has a merge problem. The difference is *where that writer sits*, and putting it at the Node keeps configuration next to the software that uses it.

### What a Node actually holds

- **Its configuration** — its cameras, their settings, its retention policy. **Owned, not cached.** Replicated one way upward for durability and lookup
- **The archive index** — which segment covers which camera over which range. Written constantly; rebuildable by scanning
- **Events** — motion, camera offline, analytics hits, operator actions. High volume, mostly never read

> This does not contradict М10's rule that *desired state is persisted and actual state is derived*. The Node persists desired state because it *is* the authority for it; what stays derived is everything about what is actually running.

### One engine, two databases

An earlier draft of this record said SQLite was plenty for the cache. That was answering a narrower question than the one the host actually poses: **the index and the events need a real database regardless**, so the host is running Postgres either way, and a second engine for a small cache buys nothing.

| | Domain database | Host database |
|---|---|---|
| Instances | one per domain | one per host |
| Written by | the controller — one writer | this host only |
| Holds | desired state, placement, people, retention policy, archive **rollup** | desired-state **cache**, archive **index**, events |
| Size | small | large and always growing |
| Write rate | rare — an operator changed something | ~100 rows/sec at a thousand cameras, plus events |
| Backup | small, careful, essential | reconstructible by rescanning segments |
| Losing it costs | configuration | a rescan |

**Why Postgres locally rather than SQLite**, once index and events are in the picture:

- **Concurrency.** SQLite permits one writer at a time; WAL lets readers run alongside a writer but does not change that. Twenty media workers writing index rows, an event stream, and the AppHost reading is real contention
- **Partitioning is the decisive feature.** Index and events are both rolling time windows. `DROP PARTITION` against `DELETE FROM` on a table taking a hundred rows a second is not a close comparison, and it makes М10 Lesson 23's retention loop instant instead of a vacuum problem
- **Types that match the work.** `tstzrange` with a GiST index answers *what footage covers this window* directly — which is М8's timeline query — and JSONB carries event payloads that differ per detector
- **One engine, one skillset.** The same `psql`, `pg_dump`, monitoring and client library. Students learn one thing; whoever operates the appliance operates one thing

### The one thing that stays outside the database

Putting the cache in Postgres puts Postgres in the boot path for recording. A few seconds of `After=postgresql.service` is fine. A data directory corrupted by power loss is not — the host would be unable to record at all, where a flat file would have carried on.

So keep exactly one thing outside: a **last-known-assignment file**, a few hundred bytes, rewritten whenever the assignment changes. Not a database — a crash-recovery hint, so a worker can start recording while Postgres is still coming up or is broken. Everything queryable lives in Postgres; only the boot-path fallback does not.

---

## The rule this makes general

The course had already made this decision twice without naming it as one principle:

| Layer                  | May be unavailable to | What the layer below does                         |
| ---------------------- | --------------------- | ------------------------------------------------- |
| Controller (М11)       | Workers               | keep recording from cached assignments            |
| Federation (М12)       | Domains               | keep operating on cached identity and entitlement |

> **Every layer is allowed to be unavailable to the layer beneath it, and the layer beneath caches what it needs to carry on.**

Stated once, it becomes a design rule rather than three separate accidents — and it is the thing that makes an edge product different from a datacentre one.

---

## Stale caches, and the operation that must never run from one

Here is the finding that matters most, because getting it wrong destroys customer data silently.

A cached desired state has an age. What is safe to do with an old one **depends on the operation**:

| From a three-week-old cache | Safe? | Why |
|---|---|---|
| Keep recording what you were already recording | **Yes**, indefinitely | Recording is the safe default. A camera still running is never the wrong answer |
| Start recording something new | Questionable | The instruction may have been withdrawn; nothing is lost by waiting |
| **Delete footage under the cached retention policy** | **No** | Irreversible, and the policy may have changed |

The failure is concrete. An operator raises retention from 7 days to 30 on Monday. On Tuesday a host loses contact with the controller. On Wednesday it deletes everything older than 7 days, exactly as its cached policy instructs — correctly, obediently, and catastrophically.

> **Destructive operations stop at the grace period. Recording does not.**

A host that cannot confirm its retention policy keeps footage and reports that it is doing so. Disks fill; that is a visible, recoverable problem. Deleted footage is neither.

---

## Where the domain boundary falls

If a domain spans a network link you do not trust, its hosts depend on that link for configuration — and the property this whole design exists to protect is gone.

> **A domain is the largest set of nodes that share a reliable network.**

Three buildings on one campus LAN: one domain. Fifty stores across a country: fifty domains, federated. The boundary is not organisational and not a matter of taste — **each level of the progression begins at a network you stopped trusting**, which is what makes Edge → Node → Domain → Federation physical rather than a tidy-looking hierarchy.

This settles two of М11's open questions at once: one controller per domain, and a domain per reliable network.

---

## High availability of the domain database

What does losing it actually cost? Not recording — hosts carry on. It costs configuration changes, reassignment after a host failure, and new placement decisions. That is an outage of *administration*, not of the product.

| Option | What it needs | Verdict |
|---|---|---|
| **Single Postgres, backup and restore** | Nothing | **The default.** Honest for an appliance |
| **repmgr** | A witness node holding no data, as referee before a standby promotes | Reasonable when a customer asks for HA |
| **pg_auto_failover** | A monitor node that actively coordinates state changes | Same class, arguably simpler to reason about |
| **Patroni** | A distributed configuration store — etcd, Consul, ZooKeeper or Kubernetes | **Note the trap** |
| Domain database in the federated layer | A working uplink | **Never.** Inverts М12's thesis |

**The Patroni trap is worth stating explicitly**, because it is exactly the kind of dependency that arrives sideways: Patroni requires a DCS, and the obvious candidates are etcd or Consul. [`consul-and-openbao.md`](../М12_FederatedVMS/consul-and-openbao.md) has just argued Consul out of the stack — choosing Patroni means either bringing it back or adding etcd instead, and now the database's availability depends on a consensus cluster the product otherwise has no use for. repmgr's witness and pg_auto_failover's monitor avoid that entirely.

On a four-box deployment bought to record cameras, spending one on a database witness is a hard sell. **Ship single-node by default; offer HA to customers who ask for it**, and pick by what the team can operate rather than by what is most sophisticated.

---

## Detail is local, summary is domain

This answers М11's third open question, and then turns out to answer more than that.

If the archive index lives in the domain database, then during a database outage recording continues but **the footage becomes unfindable** — the worst kind of failure, because it is silent and to a customer it is indistinguishable from data loss.

So split it by who wrote it:

- **The host that recorded the footage owns its index**, locally. Writing it never needs the network
- **The domain database holds a rollup only** — *"host 3 has camera 7 for these time ranges"*
- **Playback asks the domain *where*, then the host *what***

Which extends the rule М10 Lesson 26 already teaches — *do not put video bulk on replicated storage; replicate metadata and let footage be local* — one level up: **replicate the summary, not the index.**

### The same shape, three times

| Data | Local | Forwarded to the domain |
|---|---|---|
| Footage | every segment | nothing |
| Archive index | every segment's time range | which host holds which camera, when |
| Events | every event | alarms needing acknowledgement, and counts |

> **Detail is local; summary is domain.** Three data types, one rule — worth naming once rather than rediscovering per data type, because the fourth one will arrive eventually.

### Events, which the design was missing

Events — motion, camera offline, analytics hits, operator actions — were not in the plan at all and belong here. They also need distinguishing sharply from М13's material, because they look alike:

> **Events are product data: an operator searches them. Metrics are operational data: an engineer alarms on them.** Different consumers, different retention, different modules.

Most events are never read. A filtered subset — alarms an operator must acknowledge — forwards to the domain so the console can show them without querying every host. The rest ages out of a partitioned table locally.

---

## What this means

| Criterion | Answer |
|---|---|
| Postgres per Node? | **Yes — that is where configuration lives.** The domain gets a directory, not a second configuration store |
| Why not SQLite? | Index and events need a real database anyway, so a second engine is pure cost |
| How do Nodes sync? | One way, upward. Each Node is the only writer of its own rows |
| What survives a domain outage? | Recording, playback, **and configuration edits made at the Node** |
| What stops? | Creating a camera, rebalancing, cross-Node lookup, and issuing a new epoch — so failover cannot *complete* |
| What happens when a server dies? | Nomad moves the Node; its cameras go with it; **ownership is never rewritten** |
| Where does the domain end? | At the first network you would not bet recording on |
| HA? | Matters much less now the domain is a directory — except for the epoch issuer, which is the load-bearing part |

**Course changes.** М10 Lesson 20 builds **both** databases from the first lesson, and what М10 builds *is a Node* — so М11 relocates it rather than restructuring it. М11 Part A now carries what makes failover real: what must outlive a server, shared storage versus one-way replication, and fencing. Part B shrinks to the three things a Node cannot know about itself.

**Product recommendation: the Node is the authority for its own configuration; the domain is a directory, a durable copy, and the issuer of epochs.** The last of those three is the one that must stay available, because a failover cannot complete without a new epoch — everything else in the domain can be down while the product keeps working.

---

## Sources

- [PostgreSQL HA: repmgr vs Patroni vs pg_auto_failover](https://tomasz-gintowt.medium.com/postgresql-high-availability-repmgr-vs-patroni-vs-pg-auto-failover-a16fd0bfbc1e) — external dependencies of each, witness versus monitor versus DCS, and the closing argument that a system the team understands beats a more advanced one it does not
- [`consul-and-openbao.md`](../М12_FederatedVMS/consul-and-openbao.md) — why Patroni's DCS requirement is a step backwards for this stack
- [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) — camera lifecycle must survive a control-plane outage, which is the rule this record generalises
- М10 Lesson 26 — replicate metadata, let footage be local

*Written 5 September 2026.*
