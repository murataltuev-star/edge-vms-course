# Edge VMS Course

Course material for building a video management system, shipping it as an appliance, and operating a fleet of them.

Three questions, in order. **Build it:** cameras, pipelines, an archive, a timeline you can click. **Ship it:** atomic OS updates and rollback that works with nobody on site. **Operate it:** state, secrets, observability and a control plane spanning boxes you cannot physically reach.

---

## The arc

A shipped edge VMS is seven layers deep. One module per layer, each ending with something that runs.

| Module | Layer it builds | State |
|---|---|---|
| [**М8** — Cloud VMS](./М8_KVS_VMS) | The product itself, against a cloud archive | **Complete** · 15 lessons |
| [**М9** — Edge VMS](./М9_EdgeVMS) | 1 · RAUC — OS, atomic, rollback<br>2 · Nomad + Podman — workload plane | **Designed** · 9 lessons (16–24) |
| М10 — State | 3 · Postgres — domain config & state | Planned · ~4 |
| М11 — Domain controller | 4 · Cameras, archives, detectors | Planned · ~6 |
| М12 — Secrets & PKI | 5 · OpenBao — secrets, certificates | Planned · ~4 |
| М13 — Observability | 6 · Prometheus + logs | Planned · ~4 |
| М14 — Device management | 7 · Enrollment, inventory, versions | Planned · ~5 |

**[COURSE-PLAN.md](./COURSE-PLAN.md)** carries the full reasoning: why the modules run in this order, what each contains, and two structural decisions worth taking before М10 — a licensing concentration (Nomad, Consul and Vault are all BUSL under IBM) and the fact that secrets appear three modules before the module that manages them.

---

## М8 — Cloud VMS

A simulated camera publishing to Amazon Kinesis Video Streams, and a single-page console that renders a timeline of archived footage and plays back any moment on it.

Fifteen lessons take a student who knows Python but has never built a web application from a first FastAPI route to a running system: HTTP and Pydantic, process supervision and signals, containers, GStreamer pipelines, boto3 and the KVS archive APIs, then the timeline and playback, ending with a run against the specification's own acceptance criteria.

- [Lesson index](./М8_KVS_VMS/README.md) — start here
- [Project specification](./М8_KVS_VMS/module-design.md) — the complete brief the project is built from
- [KVS capability map](./М8_KVS_VMS/kvs-capability-map.md) — every Kinesis Video Streams feature a VMS can use, tiered by distance from the MVP
- [`reference/web`](./М8_KVS_VMS/reference) — the finished frontend, for comparison rather than copying

## М9 — Edge VMS

Nine lessons turning that cloud VMS into an appliance. Its spine is that a real edge product has **two independent update planes**: RAUC replaces the operating system underneath, while a scheduler manages the workload on top. Conflate them and you get systems where a config change requires an OS flash, or where an OS update destroys the recordings.

Part A builds a single appliance — A/B partitions, signed update bundles, rollback proven by shipping a deliberately broken update, then Podman and Quadlet. Part B goes to many servers and many sites with Nomad.

- [Module design](./М9_EdgeVMS/module-design.md) — lesson plan, partition layout, verification strategy, ARM porting appendix
- [Kubernetes vs Nomad](./М9_EdgeVMS/kubernetes-vs-nomad.md) — why the orchestrator changed, and what it cost
- [RAUC alternatives](./М9_EdgeVMS/rauc-alternatives.md) — SWUpdate, Mender, bootc, systemd-sysupdate, and where each wins

Both decision records reach the same shape of conclusion: the tool that teaches best is not always the tool that ships best, and the documents say which is which.

## М10–М14 — not yet started

Postgres for the state the appliance owns; a domain controller that reconciles desired cameras against running pipelines; OpenBao for secrets and per-device certificates; metrics and logs sized for a thin uplink; and finally enrollment, inventory and version skew across a fleet. Scope and sequencing in the [course plan](./COURSE-PLAN.md).

---

## How these lessons are written

Two conventions run through every lesson, and they are the reason the material is the length it is.

**Every step produces a result you can see.** A process you can signal, a container you can inspect, a pipeline whose output you can play, a page you can look at — not code taken on trust. Where a dependency could not be installed, the lesson still proves its logic some other way rather than asserting it.

**Each lesson replaces a stand-in from the one before.** `camera_sim.py` becomes the real pipeline, `filesink` becomes `kvssink`, fake clients become boto3, fixtures become real fragments. Nothing is ever more than one layer away from something already verified.

Decisions are recorded with their costs attached. Where a choice is weak — a missed acceptance criterion, a capability given up, a licence worth a lawyer's eye — the documents say so instead of quietly moving on.
