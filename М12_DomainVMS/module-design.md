# М12_DomainVMS — Module Design

**The smallest layer that can sit above a set of Nodes, and be switched off.**

[М11](../М11_ClusterVMS/module-design.md) made a Node survive its server. It did that without any coordinating layer at all — a Node owns its own configuration, so failover rewrites nothing and needs nobody's permission. That is the unusual starting position this module has to justify itself from: **almost everything already works, so what is a domain layer actually for?**

Exactly three things. This module builds them, and spends as much effort on what it refuses to do as on what it does.

> **Scope note.** These six lessons and [М11](../М11_ClusterVMS/module-design.md)'s four were one module until this split. The merge that created it had a good reason — *a cluster and the layer above it are one arc* — and the split answers it rather than ignoring it: **the two-level idea is introduced in М11 Lesson 26 and collected here in Lesson 30**, where the student is asked to name the difference between a scheduler placing Nodes and a directory placing cameras. Taught inside one module, the two levels blur, because both are "scheduling". A boundary between them is what makes the distinction survive.

> **Domain is not cluster, and a domain is bigger.** М11 built a **cluster** — servers close enough to share a network you would bet recording on, a boundary set by physics. This module builds a **domain**: the clusters under one directory, one CA and one set of operators, a boundary set by **administration**. A campus is one domain with three clusters and three server rooms; a cloud deployment is one domain with one cluster serving fifty sites. Sites and clusters are many-to-many on purpose.
>
> **And М11's rule holds here: a Node never crosses a cluster.** So this module is not about moving work between clusters — it is about *knowing where the work is*, and about being honest when a whole cluster is unreachable.

---

## The thesis

A Node owning its own configuration answers almost everything, and М11 showed a cluster failing over with nothing above it at all — **it restores from its own object store and never asks anyone's permission.** That raises the fair question of what is left for a domain layer, and the answer is: everything that stops being knowable once there is **more than one cluster.** Exactly three things:

| | Why a Node cannot answer it |
|---|---|
| **Lookup** — where is camera 7? | Asking every Node cannot distinguish *deleted* from *unreachable* |
| **Creation** — which Node gets a new camera? | Capacity and reachability across Nodes is domain knowledge by definition |
| **Rebalance** — move camera 7 from N to M | Two single-writer databases, no coordinator, no transaction. *Within a cluster only — a camera does not move between clusters any more than a Node does* |

That is a **directory**, not a configuration store — which is why it may be down while recording continues and while an operator edits a camera at its own Node.

> **The domain is a directory, not a configuration store.** That is the whole design, and every decision below follows from it.

Which is also why this is the first layer in the course that is **allowed to be unavailable**. Recording continues without it. So does playback, and so does an operator editing a camera at its own Node. What stops is creating a camera, looking one up across Nodes, rebalancing — and restoring a Node that has died, which is the one that matters.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Clusters per domain | **One or many. Nomad regions, federated** | A campus is three server rooms and one customer. Regions share no state and gossip-couple, which is exactly what a domain needs: each cluster schedules on with the others unreachable. |
| A dead cluster | **Reported, never healed** | Its cameras are on its network and its footage on its disks. Rebalancing them elsewhere produces Nodes failing to reach a dead network and hides the real fault. |
| Domain layer | **A directory, not a configuration store** | Lookup, creation and rebalance only. It may be unavailable without recording stopping. |
| Directory storage | **No database at all.** A Nomad Variable per Node for the list; an object store for the restore point | The two halves have nothing in common: kilobytes queried constantly, and megabytes read once on failover. Splitting them removes the last per-domain database — so a domain really is a set of Nodes on a network, not an installation. |
| Convergence token | **Monotonic revision**, not token equality | Ordering expresses *distance*; equality only *difference*. See below. |
| Transport | **mTLS, from a self-signed domain CA marked temporary** | The Node↔directory streams carry configuration, grants and status. A credential says who is calling; it says nothing about the channel. М13 replaces the self-signed root with a delegated intermediate. |
| Authentication | **A hand-provisioned credential per Node, marked temporary** | The course's existing discipline: the stand-in is named where it appears. М13 replaces it with a federated identity. |
| Human identity | **A token signed by a domain issuer; Nodes hold the public key, never a password hash** | М10's per-Node `operators` table becomes N Alices and N stealable hashes. Verifying a signature needs no network, so this survives the domain being down. The issuer is hand-provisioned and marked temporary; М13 federates it. |
| Authorization | **Node-local grants carrying an expiry** | Enforcement must survive the domain being down, so it cannot be a lookup. Expiry is what bounds the revocation window. |
| Status model | **Positions and reasons kept apart** | Kubernetes shipped a phase enum and then documented why it was a mistake. |

The decisions about the servers underneath — camera ownership, Node identity, fencing and the epoch — are [М11's](../М11_ClusterVMS/module-design.md), and nothing here is allowed to contradict them.

---

## Prerequisites

- **М11 entire.** Nodes that move between servers, and the epoch that keeps two instances of one Node from corrupting an archive. This module adds a layer above that and must not weaken it.
- **М11 Lesson 26** — Nomad Variables, and why configuration does *not* go in them. Lesson 29 uses the half that does.
- **М10 Lesson 20** — `revision` as a monotonic integer. The convergence token here is that same idea, one scope up.
- **М10 Lesson 24** — positions versus reasons. The console in Lesson 32 is that model at fleet scale.

---

## Why ordering beats equality

Configuration replicates one way from each Node upward, and the domain must be able to say how far behind it is. The token could be an opaque value compared for equality, or an ordered revision. Ordering wins three ways:

1. **It expresses distance, not just difference.** "Diverged" is an alert you learn to ignore; "diverged by four revisions for forty minutes" is an incident
2. **It permits skip-ahead.** A subscriber offline across revisions 7, 8 and 9 converges straight to 9 without replaying. Edge links go down constantly, so this is not an optimisation
3. **It survives replay and reordering.** A late report carrying a lower revision is ignored rather than ambiguous

**The cost:** you lose proof that one *precise* configuration was applied at one moment. If that must be auditable it belongs in an audit log, not in the convergence token.

---
## Placement that does not churn

Placement happens **twice in a camera's life** — when it is created, and if an operator rebalances — and never in between. That makes the rule easy to state and easy to violate:

> **Only place a camera when you must.** Two triggers: the camera is new, or an operator asked for a rebalance. A dead server is *not* a trigger, because the Node moves and the camera goes with it.

**Capacity comes from measurement.** М10 shipped [`shard-memory-probe.py`](../М10_NodeVMS/reference/shard-memory-probe.py) precisely so this is observed rather than guessed. **Constraints come from physics** — a camera on an isolated VLAN is reachable from some Nodes and not others.

### Why not consistent hashing

The reflexive answer, and wrong here: cameras are **not uniform** (4K at 8 Mbps beside 720p at 1); **constraints break the ring**; and it is **not inspectable** — at 3am *"why is camera 812 on Node 3"* should be a row with a reason and a timestamp, not a hash to recompute.

**Store the placement; do not derive it.** Rebalance, when genuinely wanted, is explicit: **budgeted** at N moves per minute, observable, and interruptible.

---
## Several clusters, one domain

A domain with one cluster is the common case and the boring one. The interesting shape is a campus: three server rooms, three LANs, one customer, one directory — and that is what makes this a module rather than a chapter.

**Nomad calls a cluster a *region*, and joining regions is Nomad federation.** So the mechanism was always here rather than three modules up:

- Regions are **fully independent** — they share no jobs, clients or state, and nothing replicates between them
- They are loosely coupled by a **gossip protocol**, so a job can be submitted to any region, or any region's state queried, transparently, with requests forwarded to the right regional servers
- Which is exactly the property a domain needs: **each cluster keeps scheduling with the others unreachable**, and the domain reads across them without owning them

### What that does to the directory

The list is a Nomad Variable per Node, and Variables belong to a region. So with several clusters the directory is **a federated read**, not one store — the domain scans each region through Nomad's forwarding rather than holding a copy. Single-writer per key is unchanged, and so is the limit: tens of Nodes per cluster, low hundreds of clusters' worth before the scan stops being adequate.

The **restore points** are unaffected — they are objects in each cluster's own store, outside Nomad, and only that cluster ever reads them.

### And it settles the epoch

**Raft is per-cluster.** Federated regions share no state, so there is no domain-wide raft to issue from — which would be a serious problem if a Node could move between clusters, and is a non-problem because it cannot. **The epoch only ever needs to be monotonic for one Node, and that Node lives in exactly one cluster for its whole life.** Nomad's per-region raft is not a compromise here; it is precisely the right scope.

> Worth saying out loud, because it is the kind of thing that looks like luck: the failover rule was chosen for **archive locality** — footage is on the cluster's disks — and it happens to make the fencing token's scope correct too. When two independent arguments land on the same boundary, the boundary is usually real.

### When a whole cluster dies

Not a failover, and the module must not pretend otherwise. Those cameras are on that cluster's network; if the servers are gone, so is the ability to reach the cameras and the disks holding their footage. Nothing above can heal it.

So the domain's job is **honesty, not recovery**:

- Report the cluster as **unreachable**, distinct from its Nodes being unhealthy — you do not know which
- Show what is **unavailable rather than lost**: footage on those disks still exists and will return
- **Refuse to rebalance its cameras elsewhere.** They cannot be reached from another cluster, so a placement decision would produce Nodes trying to record cameras on a dead network — busy, failing, and hiding the real fault

---

## Lessons

*Six lessons. The three things a Node cannot know about itself, and the discipline of a layer that may be down.*

### Lesson 29 — What the domain knows that a Node cannot

- The directory: which Nodes exist, which cameras belong to which, and how far behind each Node's replica is
- **The directory is two things, and only one of them is queried.** A *list* — kilobytes, read constantly, answering *where is camera 7* — and a *restore point*, megabytes per Node, read exactly once in the life of a failover and never parsed by anything but the Node that wrote it. Building one store for both is what makes people reach for a database
- **So there is no domain database.** The list is **a Nomad Variable per Node**; the restore point is **an object per Node in an object store**. Both already exist for other reasons: Variables deliver identity and the epoch, and М9's appliance already talks to object storage
- **One writer per key, enforced by the platform.** Node 3 writes only `nodes/node-3`, and a Nomad ACL policy says so. That is Candidate 2 expressed in the storage layer rather than in a convention — the same property Postgres was being asked to provide by discipline
- **What is *not* in it: the epoch.** A Nomad Variable too, but the domain's — so the whole directory can be lost and rebuilt without the fencing tokens ever going backwards
- **What the directory exports:** `node_replica_lag_seconds` — how long since each Node last published — which is the **RPO made visible per Node**, and the number that says how much configuration a failover would lose *right now* rather than in theory. Alarm on the worst Node, not the mean; one Node silent for a day is invisible in an average across forty
- **Reading it.** *Where is camera 7* scans one Variable per Node — tens of entries, not thousands of rows — and the console caches the result. Say the number out loud: this stops being adequate somewhere in the low hundreds of Nodes per domain, and that is a limit the product states rather than discovers
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

- The read view: the directory list merged with what Nodes report, **grouped by failure domain**, so a dead server reads as one cause. There is no join to write — it is a merge in the API process, which is what a directory of tens of entries permits
- **Positions and reasons.** `phase` says where an object is; conditions say why it cannot get further. Kubernetes shipped the phase enum and then documented why it was wrong
- Write API: camera CRUD with **idempotency keys**, and **what it refuses** — a client may not set placement
- **Detectors.** Another worker class with its own opaque config; the domain does not change. So **where inference runs is a deployment question**, not a schema one
- The API is unauthenticated, and the lesson says so

**Deliverable:** the console showing two hundred cameras across four Nodes; kill a server; one cause displayed.

---

### Lesson 33 — Who may call it

Lesson 32 built a write API on every Node. That is **N endpoints where there used to be one**, and the module has to say what protects them before it moves on.

- **The surface, counted honestly.** Node-owned configuration is why an operator can edit a camera while the domain is unreachable — and it is also why the thing to protect is now per-Node. This is a real cost of the design, and it belongs next to the benefit rather than three modules later
- **The channel, before the caller.** Every stream in this module — configuration upward, grants downward, status both ways — runs **mTLS**, from a **self-signed domain CA, hand-provisioned here and marked temporary**. The certificate names the **Node**, never the server it happens to be running on: failover relocates the Node, and a hostname-shaped name would have to be reissued on every move. Certificates are short-lived and renewed against the domain's own CA, so renewal never reaches outside the domain — which is what lets the whole channel keep working while the layer above is down. М13 replaces the self-signed root with an intermediate delegated from a real one; **the CA changes, and nothing else here does**
- **Authentication, hand-provisioned and marked temporary.** A credential per Node, exactly as М9 hand-provisions AWS keys and М10 a database password. М13 replaces it with a federated identity, and the replacement is the lesson
- **Grants are Node-local.** Each Node stores *subject X may do Y here*. Enforcement is a local query — no lookup, no token exchange — which is the only way authorization survives the domain being down. It also **partitions privilege**: a compromised Node can only grant rights on itself, where a central store compromised is total

#### The asymmetry that makes rights different from configuration

| If the write does not reach the Node | Result | Visible? |
|---|---|---|
| A camera edit | records the old way | **Yes** — you can see it |
| A **grant** | the operator cannot get in | Yes — they complain |
| A **revoke** | **the removed administrator keeps the site** | **No** — and they have every incentive not to mention it |

Configuration staleness is benign and self-announcing. Revocation staleness is silent and adversarial, and worse, its window is **unbounded** — it lasts until someone successfully reaches that Node, which may be weeks.

#### Expiry is the revocation mechanism

- A grant carries `valid_until`, renewed on the same upward stream that already carries configuration. A Node that cannot renew lets its grants lapse
- That converts an unbounded window into **a number the product states**, exactly like the certificate lifetime this lesson already picked, and the RPO from Lesson 27
- **The tension, and it has no clean answer:** short renewal revokes fast and locks an operator out of their own site during a long outage; long renewal is the reverse. The lesson makes students pick a number and defend it
- Rights are therefore not special — they are one more thing *cached from above with an expiry*, governed by the rule this module already applies to entitlement and placement

#### The other credential: М10's `operators` table, times N

Lesson 24 of М10 put a login on the console, against a local `operators` table holding a password hash. On one box that was right. **On N Nodes it is a defect**, and naming it is this lesson's second half.

Four Nodes means four accounts for one person, four passwords she will make identical, and four hashes an attacker can take. Worse, it breaks the rule this lesson just established: **a grant expires and the account does not.** Revoke Alice's grants and her credential still authenticates on every Node; you have bounded the authorization window and left the authentication window unbounded.

The fix is the move this course keeps making, and it is the same one the CA made two bullets ago:

> **Delegate an authority; do not distribute a secret.** A Node holds the **issuer's public key**, not Alice's password hash. It verifies a signature — which needs no network — and then checks its own local grants for the subject that signature names.

So the Node stores *no human credential at all*:

```
Alice ──▶ domain identity service ──▶ short-lived signed token (subject: alice)
                                                │
                                                ▼
                                   Node 3: verify signature (public key, offline)
                                           check expiry
                                           look up local grants for "alice"
```

- **N Nodes holding password hashes is N places to steal them from. N Nodes holding a public key is zero.** That is a security improvement, not just a tidiness one
- **The issuer is hand-provisioned here and marked temporary**, exactly like the self-signed domain CA in this same lesson. It is the second stand-in this lesson creates and М13's Lesson 42 collects both: the CA becomes a delegated intermediate, the issuer becomes federated, and *one Alice* finally spans domains
- **М10's `operators` table is superseded, not extended.** Say so explicitly — a student who keeps it and adds a `node_id` column has built the N-Alices problem on purpose

#### Two lifetimes, and they are not independent

The grant now has `valid_until` and the token has its own expiry, and picking them separately produces nonsense:

| | Too short | Too long |
|---|---|---|
| **Token lifetime** | Alice is logged out mid-incident, and cannot re-authenticate if the domain is unreachable | A revoked employee keeps working until it expires |
| **Grant lifetime** | A site in a long outage locks out its own operator | A revoked administrator keeps the site |

**A token outliving its grant is harmless** — the Node finds no grants and refuses. **A grant outliving every token is also harmless** — nobody can present a subject. The failure is assuming one covers the other. The lesson makes students state both numbers and say which one bounds the revocation window. *(It is the shorter of the two, and most people answer the token.)*

**The honest residue: break-glass.** Alice is on site, the uplink is down, and her token expired an hour ago. No amount of design removes that case — a local emergency account is what real products ship, and it reintroduces exactly the password hash this section removed. The defensible version is that it is **one account, audited on every use, alarmed on, and rotated after** — and that the module says this out loud rather than pretending the clean design has no edge.

#### What the domain can and cannot tell you

The directory aggregates grants for review, never for enforcement. And when a Node is unreachable, the answer to *"what can Alice access?"* is **incomplete** — the console must say so rather than render a short list, because a short list read as complete is how an access review misses something.

**Identity itself is not Node-local.** Alice is an employee of the customer and exists whether or not any Node does; storing her *in* Node 3 would create N Alices whose records can disagree about who she is. Nodes store grants against a subject; **this module supplies the subject locally, and М13 federates it** — see below.

**Deliverable:** grant an operator rights on a Node, then revoke them while that Node is unreachable — and state, in advance and then by measurement, exactly when their access ends.

---

### Lesson 34 — Packaging, delivery, and the licence

- **Nomad Pack**: templating, variables and registries; per-site differences without per-site forks
- **The honest GitOps gap.** Fleet is pull-based — a site catches up by itself. Nomad Pack driven from CI is push-based; your pipeline must reach each region. For flaky edge links that is materially worse, and the module says so rather than glossing it. hawkBit in М12 restores it on the OS plane
- **Reading the licence you just built on.** Nomad Community Edition is under the **Business Source License (BUSL)**, a source-available licence whose grant converts to an open-source one after a Change Date. Licensor IBM; production use is granted provided the work is not offered to third parties on a hosted or *embedded* basis to compete with IBM's paid versions; the Change Date is four years per version, converting to MPL 2.0. Full analysis in [`kubernetes-vs-nomad.md`](../М11_ClusterVMS/kubernetes-vs-nomad.md)
- Acceptance criteria for the module

**Deliverable:** one pack, three simulated sites, per-site differences — plus a written analysis of what breaks when a site is offline for a day.

---
## Verification plan

**Track 1 — verified in the authoring sandbox.** Nearly all of it, because a directory is logic and a file:

- **Placement is a pure function** — property tests are the natural fit: adding a Node moves nothing; every camera lands on exactly one; no constraint is violated
- The divergence taxonomy, one-way replication and revision handling, resume tokens, idempotency
- **Grant expiry and the revocation window** — pure logic; Lesson 33's measurement needs a clock and a fake Node, no cameras at all
- Streaming behaviour against a fake Node, in the style of Lessons 11–15
- The mTLS chain, with real `openssl`, exactly as М9 Lesson 17 built the RAUC chain

**Track 2 — needs the real bench.** Anything that needs several real Nodes on several real servers: shadow mode against live traffic, the rebalance budget under load, and the packaging exercise.

---

## Open questions

1. **Rebalance trigger.** Operator-initiated only, or scheduled during a maintenance window? The module assumes the former.
2. **How much retention policy is domain design rather than infrastructure?** Schedules, per-camera overrides and legal hold may deserve their own lessons.
3. **How short should a grant's lifetime be?** Lesson 33 makes students pick a number and defend it; the product must pick one too, trading an operator locked out during an outage against a revoked administrator retaining access.
4. **What does an air-gapped domain use for object storage?** MinIO on the servers is the obvious answer, and then somebody has to back *that* up. This is what remains of the high-availability question after the domain database was removed.
5. **Where does the domain CA run?** It is the domain's only stateful *service* — the directory is storage, and storage does not sign things. So it is a Nomad job, which means it can be rescheduled like anything else and its unavailability stops certificate renewal for as long as that takes, bounded by the certificate lifetime. A numbers problem rather than a design flaw, but the module should say it. Its key does not live in the directory; see [`where-the-database-lives.md`](where-the-database-lives.md).

**Resolved while designing the module:**

- ~~Does the directory need high availability (HA)?~~ — **answered by removing the thing that would have needed it.** The list is in Nomad's raft, replicated across servers already there for scheduling; the restore point is in an object store, whose durability is its whole product. The repmgr-versus-Patroni discussion this module was heading for does not happen
- ~~Is the domain its database?~~ — no. It has none. Five revisions of [`where-the-database-lives.md`](where-the-database-lives.md) moved in one direction throughout, and the last one removed it

---

## Sources

- [Nomad Variables HTTP API](https://developer.hashicorp.com/nomad/api-docs/variables/variables) — the `cas` parameter compared against `ModifyIndex`, 409 on conflict, and the 64 KiB item limit
- [Configurable max entry size for Nomad Variables](https://github.com/hashicorp/nomad/issues/14763) — why a limit exists at all: the impact of Variables on a memory-resident raft store
- [Nomad Pack](https://developer.hashicorp.com/nomad/tools/nomad-pack) · [Nomad LICENSE](https://raw.githubusercontent.com/hashicorp/nomad/main/LICENSE)
- [Eliminate Phase and simplify Conditions](https://github.com/kubernetes/kubernetes/issues/7856) — why phase enums were a mistake
- [`where-the-database-lives.md`](where-the-database-lives.md) — five revisions ending with one database in the whole design
- [`kubernetes-vs-nomad.md`](../М11_ClusterVMS/kubernetes-vs-nomad.md) · [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md)

*Written 5 September 2026. Split from the combined DomainVMS module on 7 September 2026.*
