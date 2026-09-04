# Edge VMS Course

Course material for building a video management system twice: first as a cloud service, then as an appliance you ship, install and update in the field.

The two halves answer different questions. **М8** asks *how do you build a VMS?* — cameras, pipelines, an archive, a timeline you can click. **М9** asks *how do you ship one?* — atomic OS updates, rollback that works unattended, and running a fleet of boxes you cannot physically reach.

---

## Contents

| Folder | What it is | State |
|---|---|---|
| [`М8_KVS_VMS`](./М8_KVS_VMS) | The Cloud VMS course — 15 lessons across 7 modules, the project specification, and a verified reference frontend | Complete |
| [`М9_EdgeVMS`](./М9_EdgeVMS) | The Edge VMS module — design and decision records | Designed; lessons not yet written |

**[COURSE-PLAN.md](./COURSE-PLAN.md)** maps the whole arc: seven architectural layers, the modules that build each, and the two structural decisions (a licensing concentration and a secrets-ordering tension) worth taking before М10.

---

## М8 — Cloud VMS

A simulated camera publishing to Amazon Kinesis Video Streams, and a single-page console that renders a timeline of archived footage and plays back any moment on it.

Fifteen lessons take a student who knows Python but has never built a web application from a first FastAPI route to a running system: HTTP and Pydantic, process supervision and signals, containers, GStreamer pipelines, boto3 and the KVS archive APIs, then the timeline and playback, ending with a run against the specification's own acceptance criteria.

- [Lesson index](./М8_KVS_VMS/README.md) — start here
- [Project specification](./М8_KVS_VMS/module-design.md) — the complete brief the project is built from
- [KVS capability map](./М8_KVS_VMS/kvs-capability-map.md) — every Kinesis Video Streams feature a VMS can use, tiered by distance from the MVP
- [`reference/web`](./М8_KVS_VMS/reference) — the finished frontend, for comparison rather than copying

## М9 — Edge VMS

Nine planned lessons (16–24) turning that cloud VMS into an appliance. Its spine is that a real edge product has **two independent update planes**: RAUC replaces the operating system underneath, while a scheduler manages the workload on top. Conflate them and you get systems where a config change requires an OS flash, or where an OS update destroys the recordings.

Part A builds a single appliance — A/B partitions, signed update bundles, rollback proven by shipping a deliberately broken update, then Podman and Quadlet. Part B goes to many servers and many sites with Nomad.

- [Module design](./М9_EdgeVMS/module-design.md) — lesson plan, partition layout, verification strategy, ARM porting appendix
- [Kubernetes vs Nomad](./М9_EdgeVMS/kubernetes-vs-nomad.md) — why the orchestrator changed, and what it cost
- [RAUC alternatives](./М9_EdgeVMS/rauc-alternatives.md) — SWUpdate, Mender, bootc, systemd-sysupdate, and where each wins

Both decision records reach the same shape of conclusion: the tool that teaches best is not always the tool that ships best, and the documents say which is which.

---

## How these lessons are written

Two conventions run through every lesson, and they are the reason the material is the length it is.

**Every step produces a result you can see.** A process you can signal, a container you can inspect, a pipeline whose output you can play, a page you can look at — not code taken on trust. Where a dependency could not be installed, the lesson still proves its logic some other way rather than asserting it.

**Each lesson replaces a stand-in from the one before.** `camera_sim.py` becomes the real pipeline, `filesink` becomes `kvssink`, fake clients become boto3, fixtures become real fragments. Nothing is ever more than one layer away from something already verified.

Decisions are recorded with their costs attached. Where a choice is weak — a missed acceptance criterion, a capability given up, a licence worth a lawyer's eye — the documents say so instead of quietly moving on.
