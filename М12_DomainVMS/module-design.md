# М12_DomainVMS — Module Design

**The smallest layer that can sit above a set of clusters, be switched off — and be the top of the product.**

[М11](../М11_ClusterVMS/module-design.md) made a Node survive its server. It did that without any coordinating layer at all — a Node owns its own configuration, so failover rewrites nothing and needs nobody's permission. That is the unusual starting position this module has to justify itself from: **almost everything already works, so what is a domain layer actually for?**

Exactly three things. This module builds them, and spends as much effort on what it refuses to do as on what it does.

> **There is nothing above the domain in the product.** Earlier drafts had a layer above it — a root CA, a federated identity, a fleet inventory, a vault. Every one of those turned out to be either a thing the domain can do for itself, or a thing the *vendor* does across customers. One customer is one domain, because a domain can be as large as their whole estate. What sits above it is [М13 — the vendor's side](../М14_VendorVMS/module-design.md), and the property this module must deliver is the one that makes that module honest: **the product works with the vendor unreachable, or gone.**

> **Domain is not cluster, and a domain is bigger.** М11 built a **cluster** — servers close enough to share a network you would bet recording on, a boundary set by physics. This module builds a **domain**: the clusters under one directory, one CA and one set of operators, a boundary set by **administration**. A campus is one domain with three clusters and three server rooms; a cloud deployment is one domain with one cluster serving fifty sites. Sites and clusters are many-to-many on purpose.
>
> **And М11's rule holds here: a Node never crosses a cluster.** So this module is not about moving work between clusters — it is about *knowing where the work is*, and about being honest when a whole cluster is unreachable.

---

## The thesis

A Node owning its own configuration answers almost everything, and М11 showed a cluster failing over with nothing above it at all — **it restores from its own object store and never asks anyone's permission.** That raises the fair question of what is left for a domain layer, and the answer is: everything that stops being knowable once there is **more than one cluster.** Exactly three things:

| | Why a **cluster** cannot answer it |
|---|---|
| **Lookup across clusters** — where is camera 7? | A cluster answers for its own Nodes, in one raft, correctly. It cannot see the other two, and **cannot tell *not mine* from *not anywhere*** |
| **Which cluster** gets a new camera? | The criterion is **reachability** — which clusters can see this site's network — and no cluster knows what the others can reach |
| **Is this answer complete?** | Only something that knows how many clusters exist can say a result is partial. A cluster asked about a camera it does not have says *no*, which is the wrong word |

**Note what is no longer in that table.** Lookup within a cluster, placing a camera on a Node, and rebalancing between Nodes are **М11's**, built in its М11 Lesson 5 out of the Variables it already had. They needed no domain then and need none now — a single-cluster customer gets all three with nothing above the cluster at all.

> **The domain is a directory *of directories*** — and unlike the one inside a cluster, **it cannot be strongly consistent**, because no raft spans clusters. That single fact is what makes this a different module rather than the same one with bigger nouns, and every decision below follows from it.

Which is also why this is the first layer in the course **allowed to be unavailable**, and more so than it looks. Recording continues without it, playback continues, an operator still edits a camera at its own Node, and **a dead server still fails over** — М11 put both of failover's dependencies inside the cluster. What stops is cross-cluster lookup, deciding which cluster gets a new camera, and issuing certificates to new services. None of it is recording, and none of it is recovery.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Domain services | **One signer job (CA + token issuer), placement, a read view, an update server, and the remote observer — hosted by one designated cluster; Nomad picks the server** | No controller and no authoritative state. The signer's key is the only thing that cannot be re-provisioned from nothing, and it is backed up beyond the hosting cluster for exactly that reason. |
| The signer's key | **A Nomad Variable in the hosting cluster's raft — a software key, on purpose** | A TPM-sealed key pins the signer to one server and defeats failover. A software key can be stolen — and the answer is **short leaf lifetimes**, so a compromise is bounded by hours, plus a key-rotation procedure the module makes students run. Not delegation: there is nobody above to delegate from, and that is the point. |
| Domain correctness | **From CAS writes, never from instance count** | `count = 1` is not exactly-one during a reschedule. Placement is safe against two instances because it writes with check-and-set, not because Nomad promises one placer. |
| Clusters per domain | **One or many. Nomad regions, federated** | A campus is three server rooms and one customer. Regions share no state and gossip-couple, which is exactly what a domain needs: each cluster schedules on with the others unreachable. |
| A dead cluster | **Reported, never healed** | Its cameras are on its network and its footage on its disks. Rebalancing them elsewhere produces Nodes failing to reach a dead network and hides the real fault. |
| Domain layer | **A directory of directories, and not consistent** | Cross-cluster lookup, choosing a cluster, and saying when an answer is partial. Within-cluster lookup, placement and rebalance are М11's. |
| Directory storage | **No database at all.** A federated read across each cluster's Variables | The clusters already hold the answer; the domain aggregates rather than copies. Restore points stay in each cluster's own object store and the domain never reads them. |
| The camera list the UI sees | **A read model built from status snapshots each Node publishes as an object beside its heartbeat — never a fan-out to Node consoles, never Variables** | The directory answers *where*, not *what*: names, phases and `last_seen` live in each Node's Postgres. Asking N consoles per page waits for the slowest and breaks on the first dead Node; putting status in Variables is the three-stores rule broken (frequent, large, replicated to every server). A snapshot is the same shape as the heartbeat, so it goes where the heartbeat went. The read view holds it in memory, shows its age, and can be rebuilt from the objects in one pass. Writes never go through it. See *The camera list* below. |
| Convergence token | **Monotonic revision**, not token equality | Ordering expresses *distance*; equality only *difference*. See below. |
| Transport | **mTLS, from the domain's own self-signed root** | The Node↔directory streams carry configuration, grants and status. A credential says who is calling; it says nothing about the channel. **The root is the customer's and stays self-signed on purpose** — a vendor-held root above it would be a vendor that can impersonate the customer's whole trust domain. |
| Authentication | **A hand-provisioned credential per Node, marked temporary — until Lesson 6** | The course's existing discipline: the stand-in is named where it appears. Lesson 6's enrollment replaces it with an LDevID issued by the domain's own signer. |
| Human identity | **A token signed by the domain signer; Nodes hold the public key, never a password hash** | М10's per-Node `operators` table becomes N Alices and N stealable hashes. Verifying a signature needs no network, so this survives the domain being down. **The signer federates to the customer's own IdP** where one exists — one domain, one Alice, and nothing above either. |
| Authorization | **Node-local grants carrying an expiry** | Enforcement must survive the domain being down, so it cannot be a lookup. Expiry is what bounds the revocation window. |
| Status model | **Positions and reasons kept apart** | Kubernetes shipped a phase enum and then documented why it was a mistake. |

The decisions about the servers underneath — camera ownership, Node identity, fencing and the epoch — are [М11's](../М11_ClusterVMS/module-design.md), and nothing here is allowed to contradict them.

---

## Prerequisites

- **М11 entire.** Nodes that move between servers, and the epoch that keeps two instances of one Node from corrupting an archive. This module adds a layer above that and must not weaken it.
- **М11 Lesson 2** — Nomad Variables, and why configuration does *not* go in them. **М11 Lesson 5** turns the half that does into a cluster directory; this module aggregates several of those.
- **М10 Lesson 1** — `revision` as a monotonic integer. The convergence token here is that same idea, one scope up.
- **М10 Lesson 5** — positions versus reasons. The console in Lesson 3 is that model at fleet scale.

---

## Why ordering beats equality

Configuration replicates one way from each Node upward, and the domain must be able to say how far behind it is. The token could be an opaque value compared for equality, or an ordered revision. Ordering wins three ways:

1. **It expresses distance, not just difference.** "Diverged" is an alert you learn to ignore; "diverged by four revisions for forty minutes" is an incident
2. **It permits skip-ahead.** A subscriber offline across revisions 7, 8 and 9 converges straight to 9 without replaying. Edge links go down constantly, so this is not an optimisation
3. **It survives replay and reordering.** A late report carrying a lower revision is ignored rather than ambiguous

**The cost:** you lose proof that one *precise* configuration was applied at one moment. If that must be auditable it belongs in an audit log, not in the convergence token.

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

## Placement, at the level above the one М11 built

М11's М11 Lesson 5 placed cameras on **Nodes**, by measured capacity, with the stability rule and its property tests. That work is done and this module does not repeat it. What is added is the level above, and the division is about *what each level knows*:

| Level | Decides | On | Because only it knows |
|---|---|---|---|
| Nomad | which **server** runs a Node | resources, constraints | the servers |
| **Cluster** (М11 L5) | which **Node** gets a camera | measured capacity | its own Nodes' load, accurately |
| **Domain** (here) | which **cluster** gets a camera | **reachability** | which clusters exist, and what each can see |

**Reachability, not capacity, is the domain's criterion**, and that is the whole reason the level exists. A camera on a warehouse VLAN can be reached from the warehouse cluster and from nowhere else; no amount of spare capacity elsewhere makes another cluster a candidate. Capacity only breaks ties among clusters that can actually see the camera.

> **Only place a camera when you must.** Two triggers: the camera is new, or an operator asked for a rebalance. A dead server is *not* a trigger — the Node moves and the camera goes with it. **And a dead cluster is not a trigger either**, for the opposite reason: its cameras cannot be reached from anywhere else, so re-placing them produces Nodes failing against a dead network and hides the real fault.

**Store the placement; do not derive it** — at both levels. At 3am, *"why is camera 812 in the north cluster"* should be a row with a reason and a timestamp.

---

## The domain services: no controller, four processes, one key

The word *domain controller* was retired from this course on purpose, because it names something that no longer exists and implies an authority the layer deliberately does not have. What runs at the domain is small enough to list:

| Service | Kind | When it is down |
|---|---|---|
| **The CA** | holds a key, signs certificates | renewal stops — bounded by certificate lifetime minus margin |
| **The token issuer** | holds a key, signs identity tokens | nobody *new* logs in; existing tokens run to expiry; break-glass (Lesson 4) |
| **Cluster-level placement** | stateless computation | new cameras get no cluster |
| **The aggregating read view** | stateless; an in-memory read model rebuilt from the snapshots Nodes publish | the console sees only its own cluster — from its own object store, by the same code |
| **The remote observer** | stateless, scrapes the other clusters | nobody is told a cluster went silent — [М13](../М13_Observability/module-design.md) |

Every outage in that column is bounded, and none of it is recording or recovery. That is the thesis, made into a table.

**The CA and the token issuer are one service.** They are the same operational thing — a process that holds keys and signs — with one availability story bounded by the same arithmetic and one thing to protect. Run them as one Nomad job with two keys, not two jobs. Calling them *the domain signer* keeps the point visible.

### One cluster hosts the domain, and that is a decision

A Nomad job runs in one region. Regions share no state, and Nomad does not reschedule across them. So the domain services are hosted by **one designated cluster**, and if that cluster dies they die with it — there is nothing to fail over *to*, and the thesis says that is acceptable.

Acceptable is not the same as accidental. **Which cluster hosts the domain is a stated deployment decision**, recorded where the directory can report it, and not wherever an installer happened to run the job first. The default is the cluster with the most reliable power and uplink, which is usually the one with the operators in it.

### The one piece of state, and why it is recoverable

Placement and the read view can be re-provisioned in another cluster from nothing. **The signer cannot: it holds the key every certificate in the domain chains to**, and losing the hosting cluster loses it. Two answers, and they are the delegation principle again:

- **The domain's root is self-signed and it is the top.** There is no authority above it to re-issue from, and that is deliberate: a vendor-held root that signs the customer's CA is a vendor that can impersonate the customer's entire trust domain, and no serious security buyer accepts it. So recoverability comes from **backup**, not delegation: the key is kept somewhere the hosting cluster's death cannot reach — another cluster's object store, or offline — and Lesson 7 makes students **rotate** it while the domain runs, because a backup nobody has restored from is a hope
- **Lose it anyway and every Node re-enrolls.** That is the honest cost of the customer owning their own trust, and the module says the number — how long a full re-enrollment takes at N Nodes — rather than leaving it as a feeling

### Who decides which server, and what that forces

The operator names the hosting **cluster**. **Nomad names the server**, continuously, and nobody types a hostname — the same rule the course has for Nodes, one layer up. Three things follow, and one is a real decision.

**The domain services fail over within the hosting cluster like any allocation.** A server dies; Nomad reschedules the signer job elsewhere in the same cluster, exactly as it would a Node. So the domain's availability is *as good as its hosting cluster's* — no better, and no worse. Only a whole-cluster death has nothing to fail over to.

**Which decides where the key lives.** A signer that can land on any server cannot keep its key on a server's disk — that disk just died. It lives in a **Nomad Variable in the hosting cluster's raft**: encrypted, ACL'd, delivered to the task, and the same mechanism М11 uses for Node identity. That is a *software* key, and the alternative should be named to be refused: sealing it in a TPM pins the signer to one server and **defeats the failover it just gained.** The tradeoff — hardware-bound keys cannot move, software keys can be stolen — is settled by lifetimes rather than by preference: **leaf certificates live hours to days**, so a stolen signing key is worth exactly as long as it takes to rotate it, and Lesson 7 makes rotation a drill rather than an emergency. A software key is acceptable *because* everything it signs is short-lived.

**And the two-instances problem is here too.** `count = 1` does not mean exactly one during a reschedule — a partitioned server may still run the old instance, which is М11 Lesson 4's entire subject. Sort the services by what that does:

| Service | Two instances briefly | Why |
|---|---|---|
| Signer | harmless | same key, same signatures |
| Read view | harmless | read-only |
| **Placement** | **a writer** — two placers could give one camera two clusters | **safe anyway**, because a placement is a **check-and-set write** into the directory's raft: the second gets a 409 and retries |

> **At the domain, correctness comes from how a write is made, never from how many instances Nomad promises.** Placement is safe because it writes with CAS, not because there is one of it.

**What the operator may still say:** a **constraint**, never a server. *The signer runs on a server with a TPM* or *not on a server carrying fifty cameras* — a requirement Nomad satisfies, which is М10's *physics leaks* table again: the operator names what must be true, the scheduler decides where.

### Cold start, which the rehydration lesson never had to face

М11's М11 Lesson 3 walks a Node's restart step by step. A *domain's* first start has a step that sequence does not: **before the signer runs, no Node in the domain can present a certificate.** The order is Nomad up on its own install-time TLS → the signer scheduled → certificates issued → Nodes begin publishing. In that window a Node records — that is the whole design — but cannot yet be seen by anything above it. Lesson 1 walks this sequence, because a student who has not seen it will build a signer that depends on a Node that depends on the signer.

---

## The camera list, and where the console gets it

The first screen any UI wants is the one the architecture so far cannot draw: *every camera, with its name, its site, whether it is recording, and when it was last seen* — across Nodes, and across clusters. The directory does not have it. М11's directory answers **where** camera 7 is, in one raft, correctly; it holds camera ids and a pointer to a configuration object, and nothing an operator would recognise as a camera. Names, phases, conditions and `last_seen` live in each Node's own Postgres, because М10 put them there and М11's *a Node owns its configuration* keeps them there. So the list is not stored anywhere. It has to be **assembled**, and the question is by whom and from what.

Three ways to assemble it, and the shape rule from М11 Lesson 2 — *small, rare and consistent is raft; large, rare and never queried is an object; everything a Node needs at once is its Postgres* — decides between them before any of them is built.

| | What it is | Why not |
|---|---|---|
| **Fan-out** | the console discovers every Node through Nomad's service catalogue and calls N consoles per page | Every page waits for the slowest Node; the first dead Node either hangs the list or forces partial-response logic into every screen; and each refresh is N network calls. Works at three Nodes, fails at thirty. |
| **Status in Variables** | every Node adds its camera phases to its own Variable | Two hundred cameras from fifty Nodes every ten seconds is a hundred raft commits a second replicated to every server, for data nobody looks up by key. This is precisely why the heartbeat was moved out of Variables (М11 Lesson 4). |
| **Published snapshots** | every Node writes `<node>/status` to the cluster's object store beside its heartbeat — the same JSON its own `/status` returns; the read view reads N small objects and holds them in memory | Frequent, medium, never queried by key: **an object**. No Node is called. No raft is written. A dead Node costs nothing but a stale snapshot. |

The third is the decision, and it is not a new mechanism: **it is the heartbeat, carrying its payload.** The Node already has the task; it grows from `{ts, epoch}` to `{ts, epoch, cameras: [...]}`, at `HEARTBEAT_INTERVAL`. The arithmetic is the reason it is cheap: two hundred cameras at roughly two hundred bytes each is a 40 kB object per Node every ten seconds; fifty Nodes are 200 kB/s into an object store that was sized for footage restore points. Nothing about that needs a design.

**What the read view is, and is not.** It is a process that lists `*/status` in each cluster's object store, keeps the result in memory, and serves the list, search and pagination from there — no call to any Node on any request. It is **not a database** (the *No database at all* decision stands): it holds nothing it cannot rebuild from the objects in one pass, and a restart of it is exactly that pass. It is a cache that admits to being one, which is М10's *desired is persisted, actual is derived* one layer up — the snapshots are actual state, and a copy of actual state is only ever a cache.

**Staleness is shown, never hidden.** Every row carries the age of the snapshot it came from, and the UI prints it: *as of 8 s ago*. A Node whose snapshot is older than `lost_after` is shown as *unreachable — last known state*, with its cameras still listed, greyed, from the last object. The console never blocks on a Node, never times out on a page, and never presents a Node's silence as its cameras' absence — which is the *not mine* versus *not anywhere* distinction from the thesis, applied to a screen.

**Writes never go through it.** When the operator edits a camera from that list, the UI asks the directory *where is camera 7*, gets the Node, and writes to **that Node's** console. The owner does not change; one-writer-per-key is not touched; and the edit shows up in the list when the next snapshot carries it, with `replicated` (М11 Lesson 3) still the only place the UI learns that the edit reached the cluster's restore point. A read model that also accepted writes would be a second owner of configuration, and the whole of М11 is about there being one.

**It is the same code at both levels.** A single-cluster customer runs the read view against their own object store and gets the whole list with no domain at all — that is the *the console sees only its own cluster* row in the services table, and it is not a degraded mode but the same process pointed at one store. The domain's read view is that process pointed at every cluster's store, which is the first concrete thing in this module that is *a directory of directories*: it merges N lists that were each consistent inside their cluster, marks which cluster each row came from, and says when one of the clusters has gone silent — the third thing a cluster cannot know, made visible on the first screen.

**What this costs the Node:** one field in the heartbeat and nothing else — the snapshot is the `/status` it already renders. **What it costs the module:** the read view stops being *federated reads of Variables plus whatever Nodes report* and becomes reads of objects, which is simpler, and Lesson 3's deliverable — two hundred cameras across four Nodes, kill a server, one cause displayed — is now specified down to where the two hundred rows come from and how old they are allowed to be.

---

## Lessons

*Five lessons. The three things a cluster cannot know, and the discipline of a layer that may be down.*

### Lesson 1 — What a cluster cannot know

М11 built a directory and did not call it one: scanning each Node's Variable answers *where is camera 7*, in one raft, strongly consistent. **This lesson is what happens to that answer when there are three clusters**, and the change is not one of scale.

- **The consistency boundary, which is the module's real subject.** Inside a cluster there is one raft, so the directory has one current answer. Across clusters there is **no raft at all** — regions share no state by design — so the domain's directory is an *aggregation* over N cluster directories: partial, stale by a bounded amount, and sometimes incomplete. **This is the CAP boundary, drawn for you by a network you stopped trusting rather than chosen**
- **Incompleteness as a first-class result.** When one cluster is unreachable, *where is camera 7* may be **unanswerable**, and the honest response is "not found in the three clusters I could reach" — never a short list rendered as though it were complete. A short list read as complete is how an access review, or a missing-camera investigation, goes wrong
- **Two-level placement, and each level decides only what it knows.** The **domain** picks the *cluster*, on **reachability** — which clusters can see this site's network at all. The **cluster** picks the *Node*, on **capacity**, which only it measures accurately. Neither level can do the other's job, and that is why this is not the same lesson as М11's
- **The third level, named once.** Nomad places allocations on servers; the cluster places cameras on Nodes; the domain places cameras on clusters. Students who have now written two of the three stop finding schedulers mysterious
- **What the domain holds and does not.** A federated read across regions, plus each cluster's object store for restore points — which the domain never touches, because recovery is cluster-local
- **The domain's first start, step by step.** Nomad up → the signer scheduled in the hosting cluster → certificates issued → Nodes publish. Name the window in which a Node is recording and invisible, and say why nothing in the sequence is allowed to depend on a Node
- **Why ordering beats equality**, from the section above: replica lag across clusters is a *distance*, and "four revisions behind for forty minutes" is an incident where "diverged" is an alert you learn to ignore
- **Opaque config** — the domain stores and forwards what it does not parse, which is what lets a new worker class ship without touching it
- **Where the domain ends:** at the first network link you would not bet *administration* on. Not recording — recording stopped being the test when the cluster boundary took that job

**Deliverable:** three clusters, one directory; find a camera in each; then make one cluster unreachable and show the console saying **what it does not know**, rather than a shorter list.

---

### Lesson 2 — Shadow mode: the domain that writes nothing

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

### Lesson 3 — The API, and what it refuses

- The read view: the directory list merged with the status snapshots Nodes publish (*The camera list* above), **grouped by failure domain**, so a dead server reads as one cause. There is no join to write — it is a merge in the API process, which is what a directory of tens of entries permits
- **Positions and reasons.** `phase` says where an object is; conditions say why it cannot get further. Kubernetes shipped the phase enum and then documented why it was wrong
- Write API: camera CRUD with **idempotency keys**, and **what it refuses** — a client may not set placement
- **Detectors.** Another worker class with its own opaque config; the domain does not change. So **where inference runs is a deployment question**, not a schema one
- The API is unauthenticated, and the lesson says so

**Deliverable:** the console showing two hundred cameras across four Nodes; kill a server; one cause displayed.

---

### Lesson 4 — Who may call it

Lesson 3 built a write API on every Node. That is **N endpoints where there used to be one**, and the module has to say what protects them before it moves on.

- **The surface, counted honestly.** Node-owned configuration is why an operator can edit a camera while the domain is unreachable — and it is also why the thing to protect is now per-Node. This is a real cost of the design, and it belongs next to the benefit rather than three modules later
- **The channel, before the caller.** Every stream in this module — configuration upward, grants downward, status both ways — runs **mTLS**, from the **domain's own self-signed root**. It is hand-provisioned here in the sense that a student runs `openssl` to make it, and **it is not a stand-in** — this is the customer's root, permanently, and Lesson 7 gives it lifetimes and rotation. The certificate names the **Node**, never the server it happens to be running on: failover relocates the Node, and a hostname-shaped name would have to be reissued on every move. Certificates are short-lived and renewed against this root, so renewal never reaches outside the domain — which is what lets the channel keep working with everything above it gone
- **Authentication, hand-provisioned and marked temporary.** A credential per Node, exactly as М9 hand-provisions AWS keys and М10 a database password. **Lesson 6 replaces it** with an LDevID the box earns by enrolling, and the replacement is that lesson
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
- That converts an unbounded window into **a number the product states**, exactly like the certificate lifetime this lesson already picked, and the RPO from М11 Lesson 3
- **The tension, and it has no clean answer:** short renewal revokes fast and locks an operator out of their own site during a long outage; long renewal is the reverse. The lesson makes students pick a number and defend it
- Rights are therefore not special — they are one more thing *cached from above with an expiry*, governed by the rule this module already applies to entitlement and placement

#### The other credential: М10's `operators` table, times N

М10 Lesson 5 of М10 put a login on the console, against a local `operators` table holding a password hash. On one box that was right. **On N Nodes it is a defect**, and naming it is this lesson's second half.

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
- **The issuer is the domain signer**, the same job that runs the CA, and it is permanent too. Where the customer already has an identity provider — and enterprises do — the signer **federates to it** over OIDC: Alice authenticates against her employer's IdP, the signer issues a domain token naming her, and the Nodes never learn the IdP exists. One domain, one Alice, and nothing above either of them
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

**Identity itself is not Node-local.** Alice is an employee of the customer and exists whether or not any Node does; storing her *in* Node 3 would create N Alices whose records can disagree about who she is. Nodes store grants against a subject; **the domain signer supplies the subject**, from the customer's own IdP where there is one.

**Deliverable:** grant an operator rights on a Node, then revoke them while that Node is unreachable — and state, in advance and then by measurement, exactly when their access ends.

---

### Lesson 5 — Packaging, updates, and the licence

- **Nomad Pack**: templating, variables and registries; per-cluster differences without per-cluster forks
- **The honest GitOps gap.** Fleet is pull-based — a site catches up by itself. Nomad Pack driven from CI is push-based; your pipeline must reach each cluster. For flaky links that is materially worse, and the module says so rather than glossing it
- **The domain runs its own update server.** Eclipse hawkBit, pull-based, hosted like every other domain service. Appliances poll it; **the vendor publishes to it** and never reaches an appliance directly. That restores pull on the OS plane, and it is what makes an air-gapped domain updatable at all — somebody carries a bundle to the update server, and the boxes fetch it as if nothing were unusual
- **Entitlement, from the domain's side.** The vendor issues it; the domain **caches** it and degrades on a grace period, exactly like placement and identity. What degrades is decided here — record-but-don't-add-cameras is the usual answer — and the number is stated. Nothing that is already recording stops because a licence server is unreachable. The licence arrives through the same hawkBit as bundles, lives in the hosting cluster's Variables, and is verified by every Node against a key shipped in the product; the issuing side — what the document contains, what it binds to, why there is no revocation — is М14 Lesson 3
- **Reading the licence you just built on.** Nomad Community Edition is under the **Business Source License (BUSL)**: Licensor IBM; production use granted unless the work is offered to third parties hosted or *embedded* to compete with IBM's paid versions; Change Date four years per version, to MPL 2.0. The competitive test is what decides it for a VMS, and the analysis is in [`COURSE-PLAN.md`](../COURSE-PLAN.md) and [`kubernetes-vs-nomad.md`](../М11_ClusterVMS/kubernetes-vs-nomad.md)
- Acceptance criteria for the module's structural half

**Deliverable:** one pack, three clusters, per-cluster differences; an OS update delivered through the domain's own hawkBit with the vendor unreachable; and a written analysis of what degrades when the licence server is unreachable for a month — and what does not.

---

### Lesson 6 — Secure introduction: a box joins the domain

The hardest problem in the course, and it belongs here because **a box joins a domain** — and the signer that issues its certificate is already running two lessons back.

A device with no secret must obtain one, over a network it does not yet trust, from a service it cannot yet authenticate. Every option is a trade, and М9 removed the easiest: **the appliance image is byte-identical across every unit**, so nothing device-specific can be inside it.

| Approach | How it fails |
|---|---|
| **Shared secret in the image** | One extracted image is every device's identity. This is how vendors get breached; not a trade-off, a defect |
| **Per-device token written at manufacture** | Works, but it is a factory process, a secret database, and a secret in transit — the problem moved to logistics |
| **Hardware root** (TPM 2.0, or a manufacturer-installed certificate) | Strongest. A key that cannot be exported, and with attestation, evidence of *what software is running* — at the cost of a hardware requirement |
| **Registration with human approval** | The device presents itself; an administrator approves it in **the domain's console**. Pragmatic, widely deployed, judgement lives in the approval — but it trusts the network at first contact |

- **The registrar is the domain's.** RFC 8995 says so and the design agrees: it is the door a box knocks on to join *this* domain, it decides yes or no, and it hands the box to the signer for an **LDevID** — a certificate from *this* domain, replacing Lesson 4's hand-provisioned credential. Enrollment is a domain service, hosted like the others
- **BRSKI's vocabulary, because it names the parts precisely:** the *pledge* carries a factory **IDevID**; the *registrar* decides; the manufacturer's **MASA** issues a *voucher* telling the pledge which registrar to trust; the pledge enrolls over **EST** and receives its LDevID. **The only part of that which is not the domain's is the MASA** — the vendor vouching for its own hardware, which is М13's — and the voucher is the single cryptographic thing a customer ever needs from the vendor
- **TPM 2.0:** sealing, attestation, and precisely what attestation does and does not prove
- **Registration-with-approval, built properly** as the shipped fallback: a queue, an audit trail, and an expiry on unapproved requests
- **The ladder:** never a shared secret in an image; approval as the honest start; hardware identity where the box has a TPM; BRSKI when the customer wants zero-touch and the vendor runs a MASA

**Deliverable:** a box enrolls from cold with nobody typing a secret, receives an LDevID from the domain signer, and the enrollment is auditable afterwards. Then the hand-provisioned per-Node credential is deleted, and nothing stops.

---

### Lesson 7 — Lifetimes, rotation, and revocation that works offline

The domain's root is self-signed and it is the top. That removes a layer and adds a duty: **nobody above will re-issue anything**, so this lesson is where the domain learns to look after its own trust.

- **The tension, with an arithmetic answer.** Short certificates revoke by expiring but a cluster offline longer than the lifetime goes dark; long ones survive outages and keep a stolen device trusted for months. There is no lifetime good at both — **split the certificates by job**:

| Certificate | Lifetime | Renewed by | Needs anything outside the cluster? |
|---|---|---|---|
| The domain root | years | a rotation drill | — |
| Service-to-service | hours to days | the signer | **never** |
| Device identity (LDevID) | long | the signer, on enrollment and renewal | never |

- **The number to state:** `maximum tolerable outage = certificate lifetime − renewal margin`. Pick lifetimes from the outage you must survive; a product promising thirty days of autonomy cannot issue seven-day certificates
- **Renewal without downtime:** overlapping validity, and reloading without dropping connections
- **Root rotation as a drill, not a disaster.** Cross-signing or an overlap window; the domain keeps running throughout; the old root is retired on a date. Students rotate a live domain's root, because a backup nobody has restored from is a hope and a rotation nobody has run is a plan
- **Revocation is a lifetime problem, not a list problem.** CRL and OCSP both assume you can reach something; let short certificates expire, and treat the device certificate as the one slow case, compensated by entitlement
- **Clock skew**, which breaks certificate validation in ways that look like everything else

**Deliverable:** simulate a thirty-day cluster outage; everything keeps working. Rotate the root under load. Then revoke a device and show it losing access on a schedule stated in advance.

---

### Lesson 8 — A cluster you rent, and a Node that does not know where it is

М11 built clusters from servers in a room. This lesson changes one thing: **where the servers come from** — and proves the software cannot tell.

- **A cloud region is just a cluster.** Rented instances on one provider network satisfy М11's definition exactly as a rack does, and Nomad cannot tell the difference. The domain's hosting cluster **provisions** it, using the customer's own cloud account — which is why this is a domain feature and not something above it
- **Deploy М10's Node three ways** — local server, rented instance, and split so a site's Nodes are local while the domain services are not — and diff the artifacts. **They are identical.** If they are not, this lesson found a bug in М10 or М11
- **The bandwidth arithmetic, done before the demo:** fifty cameras at 4 Mbit/s is 200 Mbit/s sustained upstream and ~2 TB a day. Most sites cannot buy that, so **recording stays at the edge and operation moves to the cloud** — *mixed* is the shape a real deployment takes, and a cloud-only site is for a handful of cameras with no hardware to install
- **What differs by placement**, and it is a short list: storage class and its cost curve, how the camera's stream reaches the Node, who is paged when hardware dies. **What must never differ:** configuration ownership, the epoch, the certificate chain, the update mechanism
- **A cloud site has no spool.** Its cameras stream over the internet to a Node that writes locally; an uplink outage is not buffered, it is lost — so **the camera becomes the buffer**, edge recording backfilled over ONVIF when the link returns. Say this to the customer before they choose it
- **Cost as a design input:** six cameras for thirty days is ~5.8 TB, about $130/month on hot object storage and one $150 disk on-prem. The cloud option is not cheaper; it is *operationally simpler*, and a datasheet that implies otherwise loses money per camera
- **Closing the arc with М8.** The course opened renting a cloud VMS from Kinesis. Rebuild that shape here — your Nodes, your object storage, your cloud account — and М9's hand-provisioned AWS credentials are retired by no longer being needed

**Deliverable:** one domain, two clusters — one local, one rented from the customer's cloud account by the domain itself — both recording, both in one directory, and a written bandwidth-and-cost estimate for a fifty-camera site saying which it should be.

---
## Verification plan

**Track 1 — verified in the authoring sandbox.** Nearly all of it, because a directory is logic and a file:

- **Placement is a pure function** — property tests are the natural fit: adding a Node moves nothing; every camera lands on exactly one; no constraint is violated
- The divergence taxonomy, one-way replication and revision handling, resume tokens, idempotency
- **Grant expiry and the revocation window** — pure logic; Lesson 4's measurement needs a clock and a fake Node, no cameras at all
- Streaming behaviour against a fake Node, in the style of М8 Lessons 5–8
- The mTLS chain, with real `openssl`, exactly as М9 Lesson 2 built the RAUC chain — and now the whole of Lesson 7: lifetimes, expiry, root rotation with an overlap window, and clock-skew failures, all by issuing short certificates and moving time rather than waiting
- **Enrollment end to end with a simulated MASA** — the registrar, the voucher, EST, the LDevID — and the approval fallback with its queue and audit trail. A student without a TPM reads Lesson 6's attestation section rather than running it, and the lesson says at which paragraph that starts
- The bandwidth and cost arithmetic of Lesson 8, which is a spreadsheet

**Track 2 — needs the real bench.** Anything that needs several real Nodes on several real servers: shadow mode against live traffic, the rebalance budget under load, the packaging exercise, and a rented cluster provisioned from a real cloud account. **TPM 2.0** cannot be faked in any way worth teaching.

---

## Open questions

1. **Rebalance trigger.** Operator-initiated only, or scheduled during a maintenance window? The module assumes the former.
2. **How much retention policy is domain design rather than infrastructure?** Schedules, per-camera overrides and legal hold may deserve their own lessons.
3. **How short should a grant's lifetime be?** Lesson 4 makes students pick a number and defend it; the product must pick one too, trading an operator locked out during an outage against a revoked administrator retaining access.
4. **What does an air-gapped domain use for object storage?** MinIO on the servers is the obvious answer, and then somebody has to back *that* up. This is what remains of the high-availability question after the domain database was removed.
5. ~~**Where does the domain CA run?**~~ **Answered in *The domain services* above** — as one Nomad job with the token issuer, in a designated hosting cluster, its key backed up beyond that cluster and rotated on a drill. What remains open is narrower: **should the hosting cluster be chosen automatically** when the designated one dies, or is that a human decision on purpose?

**Resolved while designing the module:**

- ~~Does the directory need high availability (HA)?~~ — **answered by removing the thing that would have needed it.** The list is in Nomad's raft, replicated across servers already there for scheduling; the restore point is in an object store, whose durability is its whole product. The repmgr-versus-Patroni discussion this module was heading for does not happen
- ~~Is the domain its database?~~ — no. It has none. Five revisions of [`where-the-database-lives.md`](where-the-database-lives.md) moved in one direction throughout, and the last one removed it

---

## Sources

- [RFC 8995 — BRSKI](https://datatracker.ietf.org/doc/html/rfc8995) — pledge, registrar, MASA, voucher, IDevID and LDevID; the registrar belongs to the domain
- [Eclipse hawkBit](https://eclipse.dev/hawkbit/) — pull-based update delivery, run as a domain service
- [Nomad Variables HTTP API](https://developer.hashicorp.com/nomad/api-docs/variables/variables) — the `cas` parameter compared against `ModifyIndex`, 409 on conflict, and the 64 KiB item limit
- [Configurable max entry size for Nomad Variables](https://github.com/hashicorp/nomad/issues/14763) — why a limit exists at all: the impact of Variables on a memory-resident raft store
- [Nomad Pack](https://developer.hashicorp.com/nomad/tools/nomad-pack) · [Nomad LICENSE](https://raw.githubusercontent.com/hashicorp/nomad/main/LICENSE)
- [Eliminate Phase and simplify Conditions](https://github.com/kubernetes/kubernetes/issues/7856) — why phase enums were a mistake
- [`where-the-database-lives.md`](where-the-database-lives.md) — five revisions ending with one database in the whole design
- [`kubernetes-vs-nomad.md`](../М11_ClusterVMS/kubernetes-vs-nomad.md) · [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md)

*Written 5 September 2026. Split from the combined DomainVMS module on 7 September 2026.*
