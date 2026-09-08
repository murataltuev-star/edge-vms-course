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
| 5 | **OpenBao** | Who is allowed to know what, and how do they prove it? *(smaller than it looks — see below)* | М13 | Designed |
| 6 | **Prometheus + logs** | Is it working, and how would I know? | М14 | Designed |
| 7 | **Device management** | What do I have, where, on which version? | М12 | Designed |

Layers 1–2 are the two update planes М9 is built around: the OS underneath, the workload on top — both visible on a single box, which is all М9 needs. М9 also owns the **third** thing on that box, which is neither: the data. A spool of recorded-but-not-yet-uploaded segments outlives both planes, and the segments it writes are what М10 turns into the archive. Layers 3–4 are the product. Layers 5–7 are what turns one working box into a fleet somebody can operate.

**Layers 5 and 7 turned out to be one layer.** They are both in М12. The plan had identity in layer 5 and device management in layer 7, three modules apart, and each asked the same question — *how does a machine prove who it is in order to get its first secret?* Enrollment is where identity and device management meet, and separating them meant neither owned it.

**Layer 4 is split across two modules,** which is a change from this plan's first version. М10 builds the reconciliation loop on a single Node, where both ends of it are visible at once; М11 handles what happens when ownership is contested. A database with nothing acting on it is not a working system, so М10 could not stop at Postgres.

**Layer 2 moved out of М9 and into М11.** М9's own progression promises one box — *a box is whatever was flashed onto it* — and it cannot promise that while building a three-server cluster in its second half. It spent one revision in М10, on the grounds that scheduling is desired-state work; that is true, but it put the two-level idea in two modules and taught it twice. A cluster and the controller above it are one arc. Nomad's cross-site federation went further still, to М13, where many networks actually begin.

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

#### Resolved: BUSL does not prohibit shipping this product

Checked against the licence itself rather than against the general alarm around it. **The Additional Use Grant permits commercial use, including embedding**, and prohibits something much narrower:

> "You may make production use of the Licensed Work, provided Your use does not include offering the Licensed Work to third parties on a hosted or embedded basis" in competition with the licensor's paid offerings.

Two conditions must hold **together**. HashiCorp's own FAQ defines both, and the second is what decides it:

> A "competitive offering" is a product sold to third parties "that significantly overlaps the capabilities of a HashiCorp commercial product."
>
> "Embedded" means including code from a HashiCorp product **"in a competitive product."**

*Embedded* is defined relative to a competitive product — embedding alone is not the trigger. Their worked example: a company building a Terraform competitor may still use Vault to secure it. **A VMS does not significantly overlap Nomad Enterprise**, so shipping Nomad inside a VMS appliance is permitted as written. The licensor is now **IBM**, not HashiCorp.

**The risk to watch is not the appliance — it is the plugin roadmap.** The moment the product lets a customer run *their own* containers on it (a third-party analytics platform, a detector marketplace, bring-your-own-model), it starts offering orchestration as a customer-facing capability, and "significantly overlaps" becomes arguable. For a VMS that is not a hypothetical drift: third-party analytics is where every VMS eventually goes. **М13 is the second exposure**, because renting cloud capacity and running customers' Nodes makes *hosted* and *embedded* both true, leaving only the competitive test.

Not legal advice. The specific question for counsel is narrower than "can we use Nomad": *does our analytics-plugin roadmap turn the appliance into something that significantly overlaps Nomad Enterprise?*

#### The Change Date is not an escape route

Each BUSL release converts to **MPL 2.0 four years after it is published** — 1.8.0 (28 May 2024) becomes MPL on **28 May 2028**; 2.0.0 (21 Apr 2026) in **April 2030**. Two things make this useless as a plan:

- **The Change Date and the support window move in opposite directions.** By the time a version is MPL it has been out of support for roughly two years. 1.7.x is the worked example: EOL since April 2024, with an allocation-directory-escape CVE (CVE-2024-7625, affecting `>= 1.7.0, < 1.7.11`) fixed **only in Enterprise**. Shipping that to an appliance nobody visits is not a licence saving, it is a defect.
- **The only genuinely MPL Nomad is ≤ 1.6.5** (13 Dec 2023), and the 1.6 branch is a trap: 1.6.6 onward ship BUSL, whose text retroactively claims coverage from 1.6.4 even though the shipped 1.6.4 and 1.6.5 artifacts carry MPL. Anyone relying on "1.6.x is MPL" must pin **≤ 1.6.5** exactly — and then has no Variable Locks, no `disconnect` block, and three years of unpatched CVEs.

**So the version decision is an engineering decision, not a licensing one:** ship a supported release, and treat BUSL as settled by the competitive test above.

**This is now settled**, in [`consul-and-openbao.md`](./М13_OrchestratedVMS/consul-and-openbao.md). The short version: Consul and OpenBao are not alternatives — they overlap on exactly one thing, mTLS between services — and Consul's mesh CA turns out to be the *same* root → per-locality intermediate → short-leaf design М12 arrives at independently. The decision turns on scope instead: no service mesh issues an identity to a device that has never been on the network, so the product runs a PKI regardless, and a second certificate hierarchy buys nothing. The accepted cost is health-check-filtered discovery, which Nomad's native discovery does not provide.

#### A vault is not what removes most of these secrets

Worth recording because it inverts the layer table above. Auditing the course's five stand-in secrets against what actually resolves each one: object-store access becomes **workload identity**; the local database password becomes **certificate auth**; the operator account is an **IdP** question; the per-Node credential and the self-signed CA are already replaced by **mTLS and a delegated intermediate** in М13 Lesson 39.

> **Most secrets exist because something was not given an identity.** Give the machine an identity and the secret it stood in for disappears.

That leaves a genuinely narrow scope for OpenBao — **dynamic credentials, and the secrets a multi-tenant operator holds for many customers** — and makes layer 5 a smaller layer than the table implies. What a vault does that Postgres cannot is *issue and revoke*: the encryption key must not sit beside the data, and a stored `valid_until` cannot revoke anything by itself. If the orchestration layer stays single-tenant, the honest answer may be that the product does not need one, and М13 Lesson 41 is written to reach that conclusion rather than avoid it.

**The exception, and it is the course's only real secrets problem:** camera credentials. An RTSP URL carries `user:pass@` inline, so М10's `rtsp_url` column silently held every customer's camera password in plaintext until Lesson 20 was corrected. Those must work with the centre unreachable, so a central vault is the wrong answer by construction — they stay at the site under a key the database backup does not contain.

### 2. Secrets arrive three modules before the module that manages them

М9 provisions AWS credentials by hand at commissioning. М10 adds a database password, an operator account and the camera credentials. М12 adds a per-Node credential and a self-signed CA. OpenBao does not arrive until М13.

This is deliberate and follows the course's existing discipline — `camera_sim.py` before the real pipeline, `filesink` before `kvssink`, fixtures before real fragments. Hand-provisioned secrets are the stand-in; М13 replaces them, and the replacement is the lesson. What must not happen is М13 arriving as a surprise: every earlier module should mark its secret handling as temporary at the point it introduces it.

---

## The modules

### М10 — NodeVMS: Postgres and the AppHost · 5 lessons (20–24) · [designed](./М10_NodeVMS/module-design.md)

The cloud VMS spec forbade a database outright. The appliance needs one, and understanding *why the answer flipped* is half the module: in the cloud, KVS held the configuration; on-prem, the box holds it. The other half is that a row saying a camera should be recording is a wish until something makes it true.

- Schema for cameras, sites and retention; migrations as a shipped artifact that runs at boot on a box nobody visits
- **Operator-owned columns versus controller-owned columns** — the distinction that keeps Node placement out of the operator's hands
- **The critical one:** `PGDATA` lives on the data partition, so it survives A/B OS updates untouched. This is М9's three-way boundary with real consequences
- The reconcile loop, built against a fake actuator first: desired persisted, actual derived, `observed_revision >= revision` as the only test of applied
- Fifty GStreamer pipelines in one Python process — the GIL boundary demonstrated, `watchdog` for stall detection, and where Python stops being the right answer

### М11 — DomainVMS: Nodes that move, and the directory above them · 10 lessons (25–34) · [designed](./М11_ClusterVMS/module-design.md)

Where the course stops being about infrastructure and starts being about the product, and the only module where getting it wrong corrupts customer data rather than merely stopping a service. М10's loop already works on one box; this is everything that appears once there is more than one.

- **Two schedulers, strictly separated.** The orchestrator places workers; the controller places cameras onto them. The orchestrator never learns what a camera is
- **Placement that does not churn.** Capacity from М10's measurements, constraints as labels, and one rule: only move a camera when you must. The property test is *adding a worker moves nothing*. Why consistent hashing is the reflexive answer and the wrong one
- **Leases, epochs and the zombie writer.** Dead, partitioned and paused are indistinguishable, and the design must be correct without resolving that. Fencing happens **at the archive, not the controller** — the epoch is part of the segment path, so a stale writer produces orphaned files rather than corruption
- **Shadow mode.** The controller observes and writes nothing until `unmanaged == 0`. This is how М10's self-directed AppHost migrates to controller-directed without a flag day
- **The API, and what it refuses.** Camera CRUD with idempotency keys; placement is not a field a client may set. Built here and deliberately unauthenticated — М12 replaces it

**Detectors resolve an open question rather than needing a lesson:** attaching one creates another object of another worker class with its own opaque config, and the controller does not change. Where inference runs is therefore a *deployment* question, answered by worker class and placement constraints.

### М12 — OrchestratedVMS: identity, trust and the fleet · 10 lessons (35–44) · [designed](./М13_OrchestratedVMS/module-design.md)

**Merged from the old М12 and М14**, which asked the same question twice, and given Nomad's cross-site federation from М9. The fourth and last scope level: things that must be true above any single domain — and, since the rename from FederatedVMS, the layer that supplies capacity as well as authority.

Its thesis is a constraint: **everything below this layer must keep working when this layer is unreachable.** A site records video whether or not the centre answers, so identity, trust and entitlement are cached and degrade on a grace period rather than blocking.

- **Secure introduction** — how a box holding no secret obtains one, over a network it cannot yet trust. [BRSKI](https://datatracker.ietf.org/doc/html/rfc8995) as the reference (pledge, registrar, MASA, voucher, IDevID → LDevID) and registration-with-approval as the shipped fallback. A shared secret in a shipped image is a defect, not a trade-off
- **An intermediate CA per domain** — the structural move that makes offline tolerance possible, because routine issuance never leaves the site
- **Lifetimes against offline tolerance**, with the arithmetic students should be able to state: *tolerable outage = certificate lifetime − renewal margin*. Revocation at the edge is a lifetime problem, not a list problem
- **The unsealing problem, resolved rather than lamented.** If the appliance needs a vault to boot, the vault is not allowed to be unavailable — which contradicts the thesis. So the appliance does not run one; the vault is central and the box holds a hardware-rooted certificate
- **People and scope across domains** — the authorisation model М11 deliberately left out
- **Inventory, reported never commanded**, and **version skew as the normal state** — the N−1 contract rule that М11's opaque config and revision ordering pay for
- **Capacity, not just authority.** This layer runs in a cloud — public, or the customer's private one — and hands out allocations, so a Node can run on hardware at the site or on a rented instance. **Edge, cloud and mixed become a placement decision rather than three products**, and Lesson 37 proves the artifacts are identical
- **The bandwidth arithmetic that decides it:** fifty cameras at 4 Mbps is 200 Mbps of sustained upstream and ~2 TB a day. Most sites cannot buy that, so recording stays at the edge and operation moves to the cloud — mixed is the default shape, not a compromise
- **Closing the arc with М8.** The course opened renting a cloud VMS from Kinesis and ends building one, retiring М9's hand-provisioned AWS credentials by not needing them
- **hawkBit**, closing both update planes with a control plane that finally spans sites

### М14 — Observability: Prometheus and logs · ~4 lessons (46–49)

**This module does not introduce observability — it collects it.** Every module below already emits signals, defined where the failure that needs them was introduced, because a metric chosen at the moment you watch something break has a reason, and one chosen in an observability chapter has only a name:

| Emitted in | Signal | The point |
|---|---|---|
| М9 L18 | the health check's four-row ladder | it decides **rollback**, on the box, offline |
| М9 L19 | `spool_oldest_seconds`, `spool_bytes_used` | alarm on age, not count — one threshold works at any camera count |
| М10 L24 | `camera_lag` (a distribution), `camera_silent_seconds` | the second: the only one describing the product |
| М11 L28 | `node_failover_seconds` (RTO, worst case), `node_epoch_conflicts` | a counter that should be zero forever |
| М12 L30 | `node_replica_lag_seconds` | the worst Node, never the mean |

What is left for this module is what is genuinely *cross-cutting*:

- **Scrape topology, bounded by the domain.** Prometheus pulls, and you cannot pull across the link you stopped trusting — so the scrape boundary **is** the domain boundary, for exactly the reason certificate issuance is
- **The placement rule, which is the module's spine:** *monitoring must not share a failure domain with the thing monitored.* A Prometheus running as a Nomad job inside the domain it watches dies with that domain and cannot tell you it died. And its mirror image from М9 L18: **a health check must not depend on monitoring**, or an unreachable metrics server rolls back a good update across the fleet at once
- **Metrics are the fourth data type**, and [`where-the-database-lives.md`](./М12_DomainVMS/where-the-database-lives.md) predicted it — *"the fourth one will arrive eventually."* Detail is local, summary is domain: full resolution at the site with pull-on-demand, alarms and aggregates to the centre. The same rule as footage, index and events
- **Cardinality, which is how monitoring becomes more expensive than the product it watches.** Per-camera series at a thousand cameras is a thousand time series per metric. Export distributions; leave the per-object number in the database the console already queries. **A metric is not a database**
- **Alarm on the product, not the process** — М9 L18's rule, stated once for everything above it. Fragment write rate and camera-offline, not CPU graphs
- **The thin uplink:** remote-write with downsampling, or local retention with pull-on-demand — and what an operator is shown for a site whose uplink is down, which is *not* "healthy"
- **Logs:** journald, retention, and never letting a secret reach them — sharpened by М10 L20's finding that an RTSP URL carries the password inline, so the leak is a **log-formatting** bug rather than a storage one
- **A second licensing finding, sharper than the Nomad one.** Grafana, Loki, Tempo and **Mimir** are all **AGPLv3**; Prometheus, VictoriaMetrics, Thanos, Cortex, the OTel Collector and Grafana Alloy are Apache 2.0. BUSL *permitted* this product; AGPL §6 triggers on **conveying at all**, modified or not, and Grafana's free Enterprise binary is explicitly not redistributable. The course's position: teach Prometheus and **ship no dashboard** — the customer installs Grafana and points it at an Apache-2.0 endpoint. Full reasoning in [`М14_Observability/module-design.md`](./М14_Observability/module-design.md)

---

## Sequencing

The order is dependency-driven, not layer-numbered:

- **М9 before М10** — an appliance has to exist before it can be scheduled onto
- **М10 before М11** — the loop has to work on one box before a scheduler above it, or contested ownership, means anything
- **М11 before М12** — a Node has to survive its server, inside one cluster, before a layer across several clusters means anything
- **М12 before М13** — identity and trust are abstract until there are N Nodes and N endpoints worth protecting
- **М13 before М14** — so that "never log a secret" is a rule students already understand

**Partly resolved by emitting low.** Now that М9–М12 each define their own signals, М13's thirty-day-outage demo and its self-halting canary have numbers to work with before М14 arrives, so the ordering problem is smaller than it looked. What remains: **one defensible alternative** is to move observability before М13. You cannot operate what you cannot see, М10's reconciliation loop is far easier to debug with metrics in front of you, and **three of М13's lessons still lean on instrumentation it has not taught** — the thirty-day outage can only be asserted without it, and a self-halting canary is an alert rule. The cost is teaching monitoring slightly before there is much worth monitoring.

---

## Scale

| Module | Lessons | Cumulative |
|---|---|---|
| М8 — Cloud VMS | 15 | 15 |
| М9 — EdgeVMS | 4 | 19 |
| М10 — NodeVMS | 5 | 24 |
| М11 — ClusterVMS | 5 | 29 |
| М12 — DomainVMS | 5 | 34 |
| М13 — OrchestratedVMS | 11 | 45 |
| М14 — Observability | ~4 | ~49 |

Roughly **49 lessons**, or a full semester. Worth deciding deliberately rather than discovering at М13: this is a large course, and М10–М14 are each a genuine module rather than an appendix.

**М13 is now the outlier at eleven lessons** — nearly double the next largest, and its own open questions note that three of them lean on observability it has not taught. It is the next split candidate, and a better one than М11 was.

---

## Deliberately out of scope

- **Analytics and inference at depth.** М11 attaches detectors; it does not teach computer vision
- **High availability of a single-box site.** One box, replaced not clustered — a second server is sold for capacity or for failover, never bolted on to make one box redundant. Failover *between* servers in a cluster is very much in scope: М11 Lesson 26 reschedules a Node off a dead server, its cameras go with it because ownership never changed, and the module says plainly what does not fail over — the footage already on that server's disks
- **Multi-tenancy.** One operator organisation per deployment
- **The cloud side.** М8 covers KVS; nothing here builds a SaaS control plane

---

## Open questions

1. ~~**The BUSL decision, taken once.**~~ **Resolved** — the Additional Use Grant permits this product; the risk is the analytics-plugin roadmap, not the appliance. See the licensing section above. What remains open is a counsel review of that one question
2. **Does the vendor run a MASA?** BRSKI is unimplementable without one, and it is a permanent operational commitment — a signing service that must outlive every appliance shipped
3. **М14's position** — before or after М13, and sharpened by М13 being eleven lessons long. See the sequencing section
4. **Does the product ship a database HA option?** [`where-the-database-lives.md`](./М12_DomainVMS/where-the-database-lives.md) settles the architecture — each Node owns its configuration, the domain keeps a directory — but whether HA is offered for the directory, and priced, is commercial

**Resolved since the first version of this plan:**

- ~~Where inference runs~~ — a deployment question, not a schema one. Opaque worker config means the controller is unchanged whether inference runs on the appliance, at the camera or in the cloud (М11)
- ~~Where the write API belongs~~ — built in М11, unauthenticated and marked as such; authentication arrives with OpenBao in М12
- ~~Lesson numbering~~ — **superseded three times by restructuring.** Current: М9 is 16–19, М10 is 20–24, М11 is 25–**29**, М12 is **30**–34, М13 is 35–45, М14 is 46–49. The М11/М12 split moved no lesson numbers at all — Part A and Part B were already contiguous
- ~~Consul in or out~~ — out, and for a better reason than licensing alone ([`consul-and-openbao.md`](./М13_OrchestratedVMS/consul-and-openbao.md))
- ~~Identity split across М12 and М14~~ — they were one layer; merged into М12
- ~~Where the domain database lives, and whether hosts replicate it~~ — **there is no domain database.** Each **Node** owns its configuration in its own Postgres and publishes one way upward; the domain's directory is a Nomad Variable per Node plus an object per Node; a **cluster** is the largest set of servers on a reliable network and a **domain** is the clusters under one directory ([`where-the-database-lives.md`](./М12_DomainVMS/where-the-database-lives.md))

---

*Layer model from the architecture discussion; licence terms read from the projects' own LICENSE files, 4 September 2026.*
