# Course Plan — the whole picture

A shipped edge VMS is seven layers deep. The course builds them in dependency order, one module per layer, each ending with something that runs.

---

## The seven layers

| # | Layer | The question it answers | Module | State |
|---|---|---|---|---|
| 1 | **RAUC** | What OS is this box running, and can I change it safely? | М9 | Designed |
| 2 | **Nomad + Podman** | What workload is running, and where? | М9 (one box) · М11 Part A (many) | Designed |
| 3 | **Postgres** | What does this system know about itself? | М10 | Designed |
| 4 | **Domain controller** | Cameras, archives, detectors — the actual product | М10 (one Node) · М11 (many) | Designed |
| 5 | **OpenBao** | Who is allowed to know what, and how do they prove it? | М12 | Designed |
| 6 | **Prometheus + logs** | Is it working, and how would I know? | М13 | Planned |
| 7 | **Device management** | What do I have, where, on which version? | М12 | Designed |

Layers 1–2 are the two update planes М9 is built around: the OS underneath, the workload on top — both visible on a single box, which is all М9 needs. Layers 3–4 are the product. Layers 5–7 are what turns one working box into a fleet somebody can operate.

**Layers 5 and 7 turned out to be one layer.** They are both in М12. The plan had identity in layer 5 and device management in layer 7, three modules apart, and each asked the same question — *how does a machine prove who it is in order to get its first secret?* Enrollment is where identity and device management meet, and separating them meant neither owned it.

**Layer 4 is split across two modules,** which is a change from this plan's first version. М10 builds the reconciliation loop on a single Node, where both ends of it are visible at once; М11 handles what happens when ownership is contested. A database with nothing acting on it is not a working system, so М10 could not stop at Postgres.

**Layer 2 moved out of М9 and into М11 Part A.** М9's own progression promises one box — *a box is whatever was flashed onto it* — and it cannot promise that while building a three-server cluster in its second half. It spent one revision in М10, on the grounds that scheduling is desired-state work; that is true, but it put the two-level idea in two modules and taught it twice. A cluster and the controller above it are one arc. Nomad's cross-site federation went further still, to М12, where many networks actually begin.

---

## Two structural warnings

### 1. The licensing concentration is worse than it looks

The natural stack for layers 2 and 5 — Nomad, Consul, Vault — is **three components under one vendor's source-available licence.** Read from the licence files, not marketing:

| Component | Licensor | Licence | Change Date |
|---|---|---|---|
| Nomad 1.7.0+ | International Business Machines Corporation | BUSL | 4 years per version → MPL 2.0 |
| Consul 1.17.0+ | International Business Machines Corporation | BUSL | 4 years per version → MPL 2.0 |
| Vault | HashiCorp / IBM | BUSL | → MPL 2.0 |

Three of seven layers under IBM's BUSL, in a product that is *shipped to customers on hardware* — which is exactly the "embedded" word the Additional Use Grant uses. Two mitigations, both real:

- **Vault → OpenBao.** A Linux Foundation fork, MPL-2.0, with serious adopters (Nvidia migrated to it). This is why layer 5 is written as OpenBao in the table above rather than Vault.
- **Consul → drop it.** Nomad has **native service discovery** that needs no Consul, and HashiCorp's own documentation says it "suits edge computing… and minimal single-cluster setups prioritizing simplicity." It gives templated service addresses but *not* dynamic DNS, *not* HTTP/TCP/gRPC health checks with healthy-instance filtering, and *not* service mesh. For a handful of services per site, that is likely enough.

That leaves **Nomad as the only unavoidable BUSL dependency**, and no fork of it exists — unlike Terraform (OpenTofu) and Vault (OpenBao). If that single dependency is unacceptable, the decision is to teach Kubernetes instead, and it should be taken now rather than at М13.

**This is now settled**, in [`consul-and-openbao.md`](./М12_FederatedVMS/consul-and-openbao.md). The short version: Consul and OpenBao are not alternatives — they overlap on exactly one thing, mTLS between services — and Consul's mesh CA turns out to be the *same* root → per-locality intermediate → short-leaf design М12 arrives at independently. The decision turns on scope instead: no service mesh issues an identity to a device that has never been on the network, so the product runs a PKI regardless, and a second certificate hierarchy buys nothing. The accepted cost is health-check-filtered discovery, which Nomad's native discovery does not provide.

### 2. Secrets arrive three modules before the module that manages them

М9 provisions AWS credentials by hand at commissioning. М10 adds a database password. М11 adds service-to-service calls. OpenBao does not arrive until М12.

This is deliberate and follows the course's existing discipline — `camera_sim.py` before the real pipeline, `filesink` before `kvssink`, fixtures before real fragments. Hand-provisioned secrets are the stand-in; М12 replaces them, and the replacement is the lesson. What must not happen is М12 arriving as a surprise: every earlier module should mark its secret handling as temporary at the point it introduces it.

---

## The modules

### М10 — NodeVMS: Postgres and the AppHost · 5 lessons (20–24) · [designed](./М10_NodeVMS/module-design.md)

The cloud VMS spec forbade a database outright. The appliance needs one, and understanding *why the answer flipped* is half the module: in the cloud, KVS held the configuration; on-prem, the box holds it. The other half is that a row saying a camera should be recording is a wish until something makes it true.

- Schema for cameras, sites and retention; migrations as a shipped artifact that runs at boot on a box nobody visits
- **Operator-owned columns versus controller-owned columns** — the distinction that keeps Node placement out of the operator's hands
- **The critical one:** `PGDATA` lives on the data partition, so it survives A/B OS updates untouched. This is М9's three-way boundary with real consequences
- The reconcile loop, built against a fake actuator first: desired persisted, actual derived, `observed_revision >= revision` as the only test of applied
- Fifty GStreamer pipelines in one Python process — the GIL boundary demonstrated, `watchdog` for stall detection, and where Python stops being the right answer

### М11 — DomainVMS: Nodes that move, and the directory above them · 10 lessons (25–34) · [designed](./М11_DomainVMS/module-design.md)

Where the course stops being about infrastructure and starts being about the product, and the only module where getting it wrong corrupts customer data rather than merely stopping a service. М10's loop already works on one box; this is everything that appears once there is more than one.

- **Two schedulers, strictly separated.** The orchestrator places workers; the controller places cameras onto them. The orchestrator never learns what a camera is
- **Placement that does not churn.** Capacity from М10's measurements, constraints as labels, and one rule: only move a camera when you must. The property test is *adding a worker moves nothing*. Why consistent hashing is the reflexive answer and the wrong one
- **Leases, epochs and the zombie writer.** Dead, partitioned and paused are indistinguishable, and the design must be correct without resolving that. Fencing happens **at the archive, not the controller** — the epoch is part of the segment path, so a stale writer produces orphaned files rather than corruption
- **Shadow mode.** The controller observes and writes nothing until `unmanaged == 0`. This is how М10's self-directed AppHost migrates to controller-directed without a flag day
- **The API, and what it refuses.** Camera CRUD with idempotency keys; placement is not a field a client may set. Built here and deliberately unauthenticated — М12 replaces it

**Detectors resolve an open question rather than needing a lesson:** attaching one creates another object of another worker class with its own opaque config, and the controller does not change. Where inference runs is therefore a *deployment* question, answered by worker class and placement constraints.

### М12 — FederatedVMS: identity, trust and the fleet · 10 lessons (35–44) · [designed](./М12_FederatedVMS/module-design.md)

**Merged from the old М12 and М14**, which asked the same question twice, and given Nomad's cross-site federation from М9. The fourth and last scope level: things that must be true above any single domain.

Its thesis is a constraint: **everything below this layer must keep working when this layer is unreachable.** A site records video whether or not the centre answers, so identity, trust and entitlement are cached and degrade on a grace period rather than blocking.

- **Secure introduction** — how a box holding no secret obtains one, over a network it cannot yet trust. [BRSKI](https://datatracker.ietf.org/doc/html/rfc8995) as the reference (pledge, registrar, MASA, voucher, IDevID → LDevID) and registration-with-approval as the shipped fallback. A shared secret in a shipped image is a defect, not a trade-off
- **An intermediate CA per domain** — the structural move that makes offline tolerance possible, because routine issuance never leaves the site
- **Lifetimes against offline tolerance**, with the arithmetic students should be able to state: *tolerable outage = certificate lifetime − renewal margin*. Revocation at the edge is a lifetime problem, not a list problem
- **The unsealing problem, resolved rather than lamented.** If the appliance needs a vault to boot, the vault is not allowed to be unavailable — which contradicts the thesis. So the appliance does not run one; the vault is central and the box holds a hardware-rooted certificate
- **People and scope across domains** — the authorisation model М11 deliberately left out
- **Inventory, reported never commanded**, and **version skew as the normal state** — the N−1 contract rule that М11's opaque config and revision ordering pay for
- **hawkBit**, closing both update planes with a control plane that finally spans sites

### М13 — Observability: Prometheus and logs · ~4 lessons (45–48)

- Metrics from Nomad, Podman and the domain controller
- What to actually alarm on for a VMS: fragment write rate, camera offline, disk fill rate, time skew. Not CPU graphs
- **The edge constraint:** you cannot ship everything to a central Prometheus over a thin uplink. Remote-write with downsampling, or local retention with pull-on-demand
- Logs: journald, retention, and never letting a secret reach them — which is why this module follows М12

---

## Sequencing

The order is dependency-driven, not layer-numbered:

- **М9 before М10** — an appliance has to exist before it can be scheduled onto
- **М10 before М11** — the loop has to work on one box before a scheduler above it, or contested ownership, means anything
- **М11 before М12** — secrets management is abstract until there are services worth protecting
- **М12 before М13** — so that "never log a secret" is a rule students already understand

**One defensible alternative:** move observability (М13) earlier, on the grounds that you cannot operate what you cannot see, and М11's reconciliation loop is much easier to debug with metrics in front of you. The cost is teaching monitoring before there is much worth monitoring.

---

## Scale

| Module | Lessons | Cumulative |
|---|---|---|
| М8 — Cloud VMS | 15 | 15 |
| М9 — EdgeVMS | 4 | 19 |
| М10 — NodeVMS | 5 | 24 |
| М11 — DomainVMS | 10 | 34 |
| М12 — FederatedVMS | 10 | 44 |
| М13 — Observability | ~4 | ~48 |

Roughly **48 lessons**, or a full semester. Worth deciding deliberately rather than discovering at М12: this is a large course, and М10–М13 are each a genuine module rather than an appendix.

---

## Deliberately out of scope

- **Analytics and inference at depth.** М11 attaches detectors; it does not teach computer vision
- **High availability of a single-box site.** One box, replaced not clustered — a second server is sold for capacity or for failover, never bolted on to make one box redundant. Failover *between* nodes in a multi-node domain is very much in scope: М10 Lesson 26 reschedules workers off a dead node, М11 Lesson 29 reassigns its cameras, and the module says plainly what does not fail over — the footage already on that node's disks
- **Multi-tenancy.** One operator organisation per deployment
- **The cloud side.** М8 covers KVS; nothing here builds a SaaS control plane

---

## Open questions

1. **The BUSL decision, taken once.** If Nomad is unacceptable in a shipped product, that changes М9 and everything above it. Decide before М11, not after М13
2. **Does the vendor run a MASA?** BRSKI is unimplementable without one, and it is a permanent operational commitment — a signing service that must outlive every appliance shipped
3. **М13's position** — before or after the domain controller, and now also sharpened by М12 being nine lessons long
4. **Does the product ship a database HA option?** [`where-the-database-lives.md`](./М11_DomainVMS/where-the-database-lives.md) settles the architecture — each Node owns its configuration, the domain keeps a directory — but whether HA is offered for the directory, and priced, is commercial

**Resolved since the first version of this plan:**

- ~~Where inference runs~~ — a deployment question, not a schema one. Opaque worker config means the controller is unchanged whether inference runs on the appliance, at the camera or in the cloud (М11)
- ~~Where the write API belongs~~ — built in М11, unauthenticated and marked as such; authentication arrives with OpenBao in М12
- ~~Lesson numbering~~ — М9 is 16–24, М10 is 25–29, М11 is 30–34, М12 is 35–43, М13 is 44–47
- ~~Consul in or out~~ — out, and for a better reason than licensing alone ([`consul-and-openbao.md`](./М12_FederatedVMS/consul-and-openbao.md))
- ~~Identity split across М12 and М14~~ — they were one layer; merged into М12
- ~~Where the domain database lives, and whether hosts replicate it~~ — each **Node** owns its configuration and replicates one way upward; the domain holds a directory; a domain is the largest set of servers on a reliable network ([`where-the-database-lives.md`](./М11_DomainVMS/where-the-database-lives.md))

---

*Layer model from the architecture discussion; licence terms read from the projects' own LICENSE files, 4 September 2026.*
