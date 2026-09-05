# Where the Database Lives

**A decision record for М10_NodeVMS and М11_DomainVMS.** Companion to [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) and [`consul-and-openbao.md`](../М12_FederatedVMS/consul-and-openbao.md), written in answer to *"a domain exists when its Postgres exists — so is Postgres installed on every host, and how do they sync?"*

It exposed a genuine hole. М10 places Postgres on a single box and says exactly where. М11 then talks throughout about "desired state in Postgres" across many nodes and **never says where it lives.** This is that answer.

---

## Verdict

Four things, of which the third is the one that would have caused real damage.

1. **One authoritative Postgres per domain.** Not one per host.
2. **Hosts cache; they do not replicate.** There is exactly one writer, so the synchronisation problem the question implies does not exist.
3. **A stale cache may keep recording forever, and must never delete anything.** Destructive operations expire; recording does not.
4. **A domain is the largest set of nodes sharing a reliable network.** That is what decides where domain boundaries fall, and it makes the whole Edge → Node → Domain → Federation progression physical rather than arbitrary.

---

## A domain is its database

The question contained its own answer. A domain is defined by the desired state that describes it, so **one domain means one authoritative database**, and adding hosts to a domain does not add databases to it.

Putting Postgres on every host and synchronising would mean choosing one of:

- **Multi-master** — and for a *desired state* store, conflicting writes are precisely the thing that must not happen. "Camera 7 belongs to worker A" and "camera 7 belongs to worker B", merged, is the split-brain М11 Lesson 32 exists to prevent
- **A consensus system** — which is what you would end up building, and Postgres is not one
- **Primary with read replicas** — the sane version of the idea, and a legitimate option, but it is an availability choice rather than a way to make every host self-sufficient

None of these is needed, because hosts do not need the database.

---

## Hosts cache; they do not replicate

The distinction is the whole answer, and it is easy to miss because both words describe "the data is also over there".

| | Replication | Caching |
|---|---|---|
| Holds | the entire dataset | only the slice this node needs |
| Write path | exists — hence conflicts, or an election | **none** |
| Staleness | must be hidden | must be **known and bounded** |
| Failure mode | divergence | a known-old view |

**A worker never writes to the domain database.** It reports actual state *through* the controller, which is the only writer. One writer means no merge, no conflict resolution, no election — the entire class of problem is designed out rather than solved.

### What a host actually holds

Small, and specific:

- its current assignment — which cameras it owns
- the opaque config for those cameras
- its lease and epoch
- its own local archive index (see below)

The first three arrive on the watch stream from М11 Lesson 30 and are persisted locally — SQLite or a file is plenty — so that a host which reboots while the controller is unreachable comes back up recording rather than idle.

> This does not contradict М10's rule that *desired state is persisted and actual state is derived*. The local copy is not a second source of truth; it is a cache with an expiry, and the next section is about that expiry.

---

## The rule this makes general

The course had already made this decision twice without naming it as one principle:

| Layer | May be unavailable to | What the layer below does |
|---|---|---|
| Controller (М11) | Workers | keep recording from cached assignments |
| Federation (М12) | Domains | keep operating on cached identity and entitlement |
| Domain database (here) | Hosts | keep recording from cached desired state |

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

## The archive index

This answers М11's third open question, and the answer follows from everything above.

If the archive index lives in the domain database, then during a database outage recording continues but **the footage becomes unfindable** — the worst kind of failure, because it is silent and it looks like data loss to the customer.

So split it by who wrote it:

- **The host that recorded the footage owns its index**, locally. It never needs the network to write it
- **The domain database holds a rollup only** — *"host 3 has camera 7 for these time ranges"*
- **Playback asks the domain *where*, then the host *what***

Which extends the rule М9 Lesson 21 already teaches — *do not put video bulk on replicated storage; replicate metadata and let footage be local* — one level up: **replicate the summary, not the index.**

It also gives the two stores the very different treatment they deserve. Configuration is small, changes rarely, and must be backed up carefully. The archive index is large, is written constantly, and is reconstructible by walking the segments on disk.

---

## What this means

| Criterion | Answer |
|---|---|
| Postgres per host? | **No.** One per domain |
| How do hosts sync? | They do not. They cache a slice, read-only, from the one writer |
| What survives a database outage? | Recording, playback from the host holding the footage, and every existing assignment |
| What stops? | Configuration changes, reassignment, and all deletion |
| Where does the domain end? | At the first network you would not bet recording on |
| HA? | Optional, witness- or monitor-based, never consensus-store-based |

**Course changes.** М10 gains a forward reference at its database-placement decision. М11 gains the cache-versus-replica distinction in Lesson 30, the stale-cache rule in Lesson 32, and closes three open questions. Neither module grows a lesson — this is a set of rules, not new material.

**Product recommendation: single Postgres per domain, hosts cache, retention never runs from a stale cache.** The third is the one to write into the acceptance tests, because it is the only one whose absence is invisible until a customer asks where their footage went.

---

## Sources

- [PostgreSQL HA: repmgr vs Patroni vs pg_auto_failover](https://tomasz-gintowt.medium.com/postgresql-high-availability-repmgr-vs-patroni-vs-pg-auto-failover-a16fd0bfbc1e) — external dependencies of each, witness versus monitor versus DCS, and the closing argument that a system the team understands beats a more advanced one it does not
- [`consul-and-openbao.md`](../М12_FederatedVMS/consul-and-openbao.md) — why Patroni's DCS requirement is a step backwards for this stack
- [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) — camera lifecycle must survive a control-plane outage, which is the rule this record generalises
- М9 Lesson 21 — replicate metadata, let footage be local

*Written 5 September 2026.*
