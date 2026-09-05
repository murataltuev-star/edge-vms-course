# Course Plan — the whole picture

A shipped edge VMS is seven layers deep. The course builds them in dependency order, one module per layer, each ending with something that runs.

---

## The seven layers

| # | Layer | The question it answers | Module | State |
|---|---|---|---|---|
| 1 | **RAUC** | What OS is this box running, and can I change it safely? | М9 Part A | Designed |
| 2 | **Nomad + Podman** | What workload is running, and where? | М9 Part B | Designed |
| 3 | **Postgres** | What does this system know about itself? | М10 | Designed |
| 4 | **Domain controller** | Cameras, archives, detectors — the actual product | М10 (one node) · М11 (many) | Designed |
| 5 | **OpenBao** | Who is allowed to know what, and how do they prove it? | М12 | Planned |
| 6 | **Prometheus + logs** | Is it working, and how would I know? | М13 | Planned |
| 7 | **Device management** | What do I have, where, on which version? | М14 | Planned |

Layers 1–2 are the two update planes М9 is built around: the OS underneath, the workload on top. Layers 3–4 are the product. Layers 5–7 are what turns one working box into a fleet somebody can operate.

**Layer 4 is split across two modules,** which is a change from this plan's first version. М10 builds the reconciliation loop on a single node, where both ends of it are visible at once; М11 takes the same loop to many nodes and adds placement and the API. A database with nothing acting on it is not a working system, so М10 could not stop at Postgres.

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

That leaves **Nomad as the only unavoidable BUSL dependency**, and no fork of it exists — unlike Terraform (OpenTofu) and Vault (OpenBao). If that single dependency is unacceptable, the decision is to teach Kubernetes instead, and it should be taken now rather than at М14.

**Consul earns its place only if** you need mTLS service mesh, DNS-based discovery, or health-based routing across many services. Note that mTLS overlaps with what OpenBao's PKI gives you in layer 5 — so before adding Consul, check whether layer 5 already covers the need.

### 2. Secrets arrive three modules before the module that manages them

М9 provisions AWS credentials by hand at commissioning. М10 adds a database password. М11 adds service-to-service calls. OpenBao does not arrive until М12.

This is deliberate and follows the course's existing discipline — `camera_sim.py` before the real pipeline, `filesink` before `kvssink`, fixtures before real fragments. Hand-provisioned secrets are the stand-in; М12 replaces them, and the replacement is the lesson. What must not happen is М12 arriving as a surprise: every earlier module should mark its secret handling as temporary at the point it introduces it.

---

## The modules

### М10 — NodeVMS: Postgres + AppHost · 5 lessons (25–29) · [designed](./М10_NodeVMS/module-design.md)

The cloud VMS spec forbade a database outright. The appliance needs one, and understanding *why the answer flipped* is half the module: in the cloud, KVS held the configuration; on-prem, the box holds it. The other half is that a row saying a camera should be recording is a wish until something makes it true.

- Schema for cameras, sites and retention; migrations as a shipped artifact that runs at boot on a box nobody visits
- **Operator-owned columns versus controller-owned columns** — the distinction that keeps node placement out of the operator's hands
- **The critical one:** `PGDATA` lives on the data partition, so it survives A/B OS updates untouched. This is М9's three-way boundary with real consequences
- The reconcile loop, built against a fake actuator first: desired persisted, actual derived, `observed_revision >= revision` as the only test of applied
- Fifty GStreamer pipelines in one Python process — the GIL boundary demonstrated, `watchdog` for stall detection, and where Python stops being the right answer

### М11 — DomainVMS: the domain controller, across nodes · 5 lessons (30–34) · [designed](./М11_DomainVMS/module-design.md)

Where the course stops being about infrastructure and starts being about the product, and the only module where getting it wrong corrupts customer data rather than merely stopping a service. М10's loop already works on one box; this is everything that appears once there is more than one.

- **Two schedulers, strictly separated.** The orchestrator places workers; the controller places cameras onto them. The orchestrator never learns what a camera is
- **Placement that does not churn.** Capacity from М10's measurements, constraints as labels, and one rule: only move a camera when you must. The property test is *adding a worker moves nothing*. Why consistent hashing is the reflexive answer and the wrong one
- **Leases, epochs and the zombie writer.** Dead, partitioned and paused are indistinguishable, and the design must be correct without resolving that. Fencing happens **at the archive, not the controller** — the epoch is part of the segment path, so a stale writer produces orphaned files rather than corruption
- **Shadow mode.** The controller observes and writes nothing until `unmanaged == 0`. This is how М10's self-directed AppHost migrates to controller-directed without a flag day
- **The API, and what it refuses.** Camera CRUD with idempotency keys; placement is not a field a client may set. Built here and deliberately unauthenticated — М12 replaces it

**Detectors resolve an open question rather than needing a lesson:** attaching one creates another object of another worker class with its own opaque config, and the controller does not change. Where inference runs is therefore a *deployment* question, answered by worker class and placement constraints.

### М12 — Secrets and PKI: OpenBao · ~4 lessons

- Why hand-provisioned credentials stop working the moment there is more than one box
- Auth methods, and the appliance's identity problem: how does a machine prove who it is to get its first secret?
- PKI: per-device certificates, mTLS between services, rotation without downtime
- **The honest edge problem: unsealing.** A box that reboots unattended at 3am must unseal without a human. Auto-unseal normally leans on a cloud KMS, which an air-gapped site does not have. There is no clean answer, and the module should say so rather than pretend

### М13 — Observability: Prometheus and logs · ~4 lessons

- Metrics from Nomad, Podman and the domain controller
- What to actually alarm on for a VMS: fragment write rate, camera offline, disk fill rate, time skew. Not CPU graphs
- **The edge constraint:** you cannot ship everything to a central Prometheus over a thin uplink. Remote-write with downsampling, or local retention with pull-on-demand
- Logs: journald, retention, and never letting a secret reach them — which is why this module follows М12

### М14 — Device management: enrollment, inventory, versions · ~5 lessons

The capstone that closes the fleet story.

- **Enrollment:** a box arrives at a site. How does it join, get an identity, and fetch its configuration without someone typing secrets into it?
- **Inventory:** what do I have, where is it, what is it running?
- **Version skew:** a fleet on mixed versions is the normal state, not a failure. Design for it
- **hawkBit** for OS updates — the pull-based answer to the push-based gap М9 accepts when it chooses Nomad Pack over Fleet

This is where the two update planes finally get a control plane that spans sites.

---

## Sequencing

The order is dependency-driven, not layer-numbered:

- **М10 before М11** — the loop has to work on one box before placement across several is meaningful
- **М11 before М12** — secrets management is abstract until there are services worth protecting
- **М12 before М13** — so that "never log a secret" is a rule students already understand
- **М14 last** — the fleet capstone needs everything else to exist first

**One defensible alternative:** move observability (М13) earlier, on the grounds that you cannot operate what you cannot see, and М11's reconciliation loop is much easier to debug with metrics in front of you. The cost is teaching monitoring before there is much worth monitoring.

---

## Scale

| Module | Lessons | Cumulative |
|---|---|---|
| М8 — Cloud VMS | 15 | 15 |
| М9 — EdgeVMS | 9 | 24 |
| М10 — NodeVMS | 5 | 29 |
| М11 — DomainVMS | 5 | 34 |
| М12 — OpenBao | ~4 | ~38 |
| М13 — Observability | ~4 | ~42 |
| М14 — Device management | ~5 | ~47 |

Roughly **47 lessons**, or a full semester. Worth deciding deliberately rather than discovering at М12: this is a large course, and М10–М14 are each a genuine module rather than an appendix.

---

## Deliberately out of scope

- **Analytics and inference at depth.** М11 attaches detectors; it does not teach computer vision
- **High availability of the appliance itself.** One box per site, replaced not clustered
- **Multi-tenancy.** One operator organisation per deployment
- **The cloud side.** М8 covers KVS; nothing here builds a SaaS control plane

---

## Open questions

1. **The BUSL decision, taken once.** If Nomad is unacceptable in a shipped product, that changes М9 and everything above it. Decide before М11, not after М14
2. **Consul in or out.** The recommendation here is out, on the evidence that Nomad's native discovery covers edge-scale needs. Revisit if service mesh becomes a requirement
3. **М13's position** — before or after the domain controller
4. **Does the controller need high availability?** Workers keep recording when it is down, which is settled. Whether a site can tolerate losing its controller — and whether there is one per site or one per fleet — is not, and it bears on М14

**Resolved since the first version of this plan:**

- ~~Where inference runs~~ — a deployment question, not a schema one. Opaque worker config means the controller is unchanged whether inference runs on the appliance, at the camera or in the cloud (М11)
- ~~Where the write API belongs~~ — built in М11, unauthenticated and marked as such; authentication arrives with OpenBao in М12
- ~~Lesson numbering~~ — М9 is 16–24, М10 is 25–29, М11 is 30–34

---

*Layer model from the architecture discussion; licence terms read from the projects' own LICENSE files, 4 September 2026.*
