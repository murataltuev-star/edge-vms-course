# Edge VMS Course

Course material for building a video management system, shipping it as an appliance, and operating a fleet of them.

---

## Edge → Node → Domain

The module names are not decoration. They mark one idea getting harder three times, and the course is arranged around it: **where the truth about the system lives, and how many things are able to disagree about it.**

| | The box knows | Truth lives | What can disagree | The new hard problem |
|---|---|---|---|---|
| **М9 · EdgeVMS** | what it *is* | in the image that booted | nothing — a box is whatever was flashed onto it | replacing the OS underneath a running product without destroying the recordings |
| **М10 · NodeVMS** | what it *should be* | in a database on the box | desired state and actual state, inside one process | closing the gap — and never persisting the half that must be re-derived |
| **М11 · DomainVMS** | what *everyone* should be | in a database that several nodes act on | nodes, with each other | agreeing who owns what, when dead and merely-unreachable look identical |
| **М12 · FederatedVMS** | who it *is*, and what it may do | in a trust root above every domain | domains, with the centre | staying correct while the centre is unreachable |

[**М8**](./М8_KVS_VMS) comes before the progression starts: it builds the product itself with no local truth at all, because Kinesis holds the configuration and the archive both. Everything after it is the consequence of the box having to hold its own.

**EdgeVMS has no desired state.** You flash an image and containers run; actual state is the only state there is. М9's whole job is making that replaceable safely — which is why it is about atomic updates, rollback and signatures rather than about cameras.

**NodeVMS introduces the wish.** A row saying a camera should be recording is not a camera recording, and something has to close the gap. The rule this turns on runs through everything above it: *desired state is persisted, actual state is derived.* Persist the second and you have built a cache that lies.

**DomainVMS keeps that loop and takes away the shared address space.** Conceptually nothing changes; practically everything does, because two nodes can now hold different opinions, and a paused process is indistinguishable from a dead one. That is why М11 is the only module where a mistake corrupts customer footage instead of stopping a service.

**FederatedVMS is the first layer that is allowed to be unavailable**, and that constraint is the whole module. A site records video whether or not the centre answers, so identity, trust and entitlement are cached and degrade on a grace period rather than blocking. Federation rather than hierarchy — domains stay authoritative for their own operation.

**М13 is the one module that is not a new scope.** Observability is how you see the four you already have, which is why it comes last and why it does not get a VMS name.

---

## The arc

A shipped edge VMS is seven layers deep. One module per layer, each ending with something that runs.

| Module | Layer it builds | State |
|---|---|---|
| [**М8** — Cloud VMS](./М8_KVS_VMS) | The product itself, against a cloud archive | **Complete** · 15 lessons |
| [**М9** — EdgeVMS](./М9_EdgeVMS) | 1 · RAUC — OS, atomic, rollback<br>2 · Nomad + Podman — workload plane | **Designed** · 9 lessons (16–24) |
| [**М10** — NodeVMS](./М10_NodeVMS) | 3 · Postgres — domain state<br>4 · AppHost — the loop that acts on it | **Designed** · 5 lessons (25–29) |
| [**М11** — DomainVMS](./М11_DomainVMS) | 4 · Placement, leases, the API | **Designed** · 5 lessons (30–34) |
| [**М12** — FederatedVMS](./М12_FederatedVMS) | 5 · OpenBao — identity, trust, PKI<br>7 · Enrollment, inventory, version skew | **Designed** · 9 lessons (35–43) |
| М13 — Observability | 6 · Prometheus + logs | Planned · ~4 (44–47) |

**[COURSE-PLAN.md](./COURSE-PLAN.md)** carries the full reasoning: why the modules run in this order, what each contains, and two structural decisions worth taking before М11 — a licensing concentration (Nomad, Consul and Vault are all BUSL under IBM) and the fact that secrets appear three modules before the module that manages them.

---

## М8 — Cloud VMS

A simulated camera publishing to Amazon Kinesis Video Streams, and a single-page console that renders a timeline of archived footage and plays back any moment on it.

Fifteen lessons take a student who knows Python but has never built a web application from a first FastAPI route to a running system: HTTP and Pydantic, process supervision and signals, containers, GStreamer pipelines, boto3 and the KVS archive APIs, then the timeline and playback, ending with a run against the specification's own acceptance criteria.

- [Lesson index](./М8_KVS_VMS/README.md) — start here
- [Project specification](./М8_KVS_VMS/module-design.md) — the complete brief the project is built from
- [KVS capability map](./М8_KVS_VMS/kvs-capability-map.md) — every Kinesis Video Streams feature a VMS can use, tiered by distance from the MVP
- [`reference/web`](./М8_KVS_VMS/reference) — the finished frontend, for comparison rather than copying

## М9 — EdgeVMS

Nine lessons turning that cloud VMS into an appliance. Its spine is that a real edge product has **two independent update planes**: RAUC replaces the operating system underneath, while a scheduler manages the workload on top. Conflate them and you get systems where a config change requires an OS flash, or where an OS update destroys the recordings.

Part A builds a single appliance — A/B partitions, signed update bundles, rollback proven by shipping a deliberately broken update, then Podman and Quadlet. Part B goes to many servers and many sites with Nomad.

- [Module design](./М9_EdgeVMS/module-design.md) — lesson plan, partition layout, verification strategy, ARM porting appendix
- [Kubernetes vs Nomad](./М9_EdgeVMS/kubernetes-vs-nomad.md) — why the orchestrator changed, and what it cost
- [RAUC alternatives](./М9_EdgeVMS/rauc-alternatives.md) — SWUpdate, Mender, bootc, systemd-sysupdate, and where each wins
- [One container per camera?](./М9_EdgeVMS/apphost-and-process-model.md) — the process model at 1000 cameras, and why the orchestrator must not own camera lifecycle

All three decision records reach the same shape of conclusion: the tool that teaches best is not always the tool that ships best, and the documents say which is which.

## М10 — NodeVMS

Five lessons in which the box starts owning its own truth. `INSERT INTO cameras` causes a camera to start recording; `DELETE` stops it; killing the AppHost loses nothing but the open segment. Between the row and the pipeline there is only a loop the student wrote.

Its organising rule is that **desired state is persisted and actual state is derived** — persist the second and you have built a cache that lies. It is also where the process model from М9's third decision record gets built: fifty GStreamer pipelines in one Python process, with the GIL boundary demonstrated rather than asserted.

- [Module design](./М10_NodeVMS/module-design.md) — lesson plan, the Python shard model, and what the operator is never asked to decide

## М11 — DomainVMS

Five lessons on what only exists once there is a second node: the controller and a worker can disagree about who owns a camera, and a worker can be alive, unreachable and still writing. This is the only module where a mistake corrupts customer footage rather than stopping a service.

It is built backwards from one demo. Two hundred cameras across four workers; `kill -STOP` one of them — alive, holding its file handles, exactly what a hung disk looks like — and watch its cameras reappear elsewhere. Then `kill -CONT` it and let the zombie try to keep writing. **The archive is intact, and the student can prove it.**

The answer is that fencing belongs at the archive rather than at the controller: the lease epoch is part of the segment path, so a stale writer cannot name the files it would otherwise corrupt. You cannot stop a zombie from writing — you can only make its writes harmless.

- [Module design](./М11_DomainVMS/module-design.md) — the two-scheduler contract, placement stability, fencing, shadow mode, and what the API refuses

## М12 — FederatedVMS

Nine lessons on what has to be true above any single domain: who a box is, who a person is, what a customer is entitled to, and what the fleet actually consists of. Merged from two modules that were three apart and asked the same question twice — *how does a machine prove who it is to get its first secret?* — with neither owning it.

The demo: a box arrives in a carton, nobody types a secret into it, and minutes later it is recording. Then the uplink is cut for thirty days and it keeps working, because routine certificate issuance never leaves the site. Then it is marked stolen and loses access on a schedule stated in advance.

It also resolves the problem the course plan had flagged as having no clean answer. Unattended unsealing at 3am: **if the appliance needs a vault to boot, the vault is not allowed to be unavailable** — which contradicts the layer's own thesis. So the appliance does not run one.

- [Module design](./М12_FederatedVMS/module-design.md) — secure introduction, per-domain intermediate CAs, lifetimes against offline tolerance, inventory and version skew
- [Consul and OpenBao](./М12_FederatedVMS/consul-and-openbao.md) — two answers to mTLS, and why the product needs only one

## М13 — not yet started

Metrics and logs sized for a thin uplink: what to alarm on for a VMS, and why you cannot ship everything to a central Prometheus. Scope and sequencing in the [course plan](./COURSE-PLAN.md).

---

## How these lessons are written

Two conventions run through every lesson, and they are the reason the material is the length it is.

**Every step produces a result you can see.** A process you can signal, a container you can inspect, a pipeline whose output you can play, a page you can look at — not code taken on trust. Where a dependency could not be installed, the lesson still proves its logic some other way rather than asserting it.

**Each lesson replaces a stand-in from the one before.** `camera_sim.py` becomes the real pipeline, `filesink` becomes `kvssink`, fake clients become boto3, fixtures become real fragments. Nothing is ever more than one layer away from something already verified.

Decisions are recorded with their costs attached. Where a choice is weak — a missed acceptance criterion, a capability given up, a licence worth a lawyer's eye — the documents say so instead of quietly moving on.
