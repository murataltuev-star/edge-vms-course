# Edge VMS Course

Course material for building a video management system, shipping it as an appliance, and operating a fleet of them.

---

## Edge → Node → Domain → Orchestration

The module names are not decoration. They mark one idea getting harder three times, and the course is arranged around it: **where the truth about the system lives, and how many things are able to disagree about it.**

|                        | The box knows                   | Truth lives                             | What can disagree                               | The new hard problem                                                               |
| ---------------------- | ------------------------------- | --------------------------------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------- |
| **М9 · EdgeVMS**       | what it *is*                    | in the image that booted                | nothing — a box is whatever was flashed onto it | replacing the OS underneath a running product without destroying the recordings    |
| **М10 · NodeVMS** | what it *should be* | in a database on the box | desired state and actual state, inside one process | closing the gap — and never persisting the half that must be re-derived |
| **М11 · DomainVMS** | what it should be, *wherever it is running* | in each Node, with a directory above them | two instances of the same Node | surviving a server's death without two writers reaching one archive |
| **М12 · OrchestratedVMS** | who it *is*, what it may do, **and where it may run** | in a trust root above every domain, in a cloud | domains, with the centre | staying correct while the centre is unreachable — and making edge and cloud one product rather than two |

**Every boundary in that table is a network you stopped trusting.** A domain is the largest set of servers sharing a reliable link; past that you federate rather than build a bigger domain. That is what makes the progression physical rather than a tidy-looking hierarchy — and it is also why **rented servers in one cloud region are a domain like any other**, which is what lets М12 offer edge, cloud and mixed deployments from one codebase.

### Three words the course keeps apart

| | What it is | Who decides |
|---|---|---|
| **Node** | a VMS instance — its own database, its own cameras, its own archive index. From М11 it is a scheduler allocation with stable identity, and it **moves between servers** | an operator, when capacity is bought |
| **Server** | a box with CPUs and disks. Runs whichever Nodes the scheduler puts on it | the scheduler, continuously |
| **Site** | where cameras physically are. The only one of the three an operator names | the customer's building |

**A Node is not a server**, and М10 builds exactly one Node without ever needing the distinction. It matters from М11 onward, where a server dying moves the Node rather than reassigning its cameras — which is why failover rewrites nothing.

[**М8**](./М8_KVS_VMS) comes before the progression starts: it builds the product itself with no local truth at all, because Kinesis holds the configuration and the archive both. Everything after it is the consequence of the box having to hold its own.

**EdgeVMS has no desired state.** You flash an image and containers run; actual state is the only state there is. М9's whole job is making that replaceable safely — which is why it is four lessons about atomic updates, rollback and signatures, and not one about cameras.

**NodeVMS introduces the wish.** A row saying a camera should be recording is not a camera recording, and something has to close the gap — five lessons in which the student writes that reconciler by hand, at a scale where both ends fit in one terminal. The rule it turns on runs through everything above: *desired state is persisted, actual state is derived.* Persist the second and you have built a cache that lies.

**DomainVMS is where the second box appears, and it teaches one idea twice on purpose.** First a production reconciler — a Nomad cluster, the VMS as a job on it, a node pulled off the wall — because a jobspec *is* desired state and a scheduler *is* the loop. Then the pivot: **Nomad's allocations belong to whoever placed them, and nothing argues.** A camera is *owned*, two workers can claim it, and a paused process is indistinguishable from a dead one. That is why М11 is the only module where a mistake corrupts customer footage instead of stopping a service.

**Every layer is allowed to be unavailable to the layer beneath it**, and the layer beneath caches what it needs to carry on. Workers keep recording when the controller is down; Nodes keep recording — and keep being edited — when the domain is unreachable; domains keep operating when the centre is unreachable. OrchestratedVMS is where that stops being one decision among several and becomes a module's entire thesis — which is what makes this federation rather than hierarchy.

The rule has a sharp edge, and it is the one worth carrying away: **anything a layer caches from above may keep recording forever, and must never delete anything.** Destructive operations expire; recording does not. A Node owns its own retention policy, so it cannot go stale on that — but entitlement and placement come from above, and those can.

**М13 is the one module that is not a new scope.** Observability is how you see the four you already have, which is why it comes last and why it does not get a VMS name.

---

## The arc

A shipped edge VMS is seven layers deep. One module per layer, each ending with something that runs.

| Module | Layer it builds | State |
|---|---|---|
| [**М8** — Cloud VMS](./М8_KVS_VMS) | The product itself, against a cloud archive | **Complete** · 15 lessons |
| [**М9** — EdgeVMS](./М9_EdgeVMS) | 1 · RAUC — OS, atomic, rollback | **Designed** · 4 lessons (16–19) |
| [**М10** — NodeVMS](./М10_NodeVMS) | 3 · Postgres — domain state<br>4 · AppHost — the loop that acts on it | **Designed** · 5 lessons (20–24) |
| [**М11** — DomainVMS](./М11_DomainVMS) | 2 · Nomad + Podman — Nodes that move<br>4 · A directory that is not a database, and the domain's own CA | **Designed** · 10 lessons (25–34) |
| [**М12** — OrchestratedVMS](./М12_OrchestratedVMS) | 5 · OpenBao — identity, the trust root above every domain<br>7 · Enrollment, inventory, version skew<br>+ capacity: allocations, so a Node can run anywhere | **Designed** · 11 lessons (35–45) |
| М13 — Observability | 6 · Prometheus + logs | Planned · ~4 (46–49) |

**[GLOSSARY.md](./GLOSSARY.md)** defines every term the course uses precisely — Node versus Server, desired versus actual state, epoch and fencing, and the acronyms it would otherwise leave unexplained.

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

Four lessons turning that cloud VMS into an appliance: A/B partitions, signed update bundles, rollback proven by shipping a deliberately broken update, and then Podman and Quadlet.

Its spine is that a real edge product has **two independent update planes** — the operating system underneath, the workload on top — and both are visible on one box. Lesson 19 is where it bites: Podman's storage must be redirected to the data partition, because images and volumes left in a rootfs slot are destroyed by the next OS update. Conflate the planes and you build systems where a config change requires an OS flash.

*The multi-node half of this module moved to М11, where Nodes are scheduled across servers. A module called EdgeVMS should not build a raft cluster.*

- [Module design](./М9_EdgeVMS/module-design.md) — lesson plan, partition layout, verification strategy, ARM porting appendix
- [RAUC alternatives](./М9_EdgeVMS/rauc-alternatives.md) — SWUpdate, Mender, bootc, systemd-sysupdate, and where each wins
- [One container per camera?](./М9_EdgeVMS/apphost-and-process-model.md) — the process model at 1000 cameras, and why the orchestrator must not own camera lifecycle

Both reach the same shape of conclusion, as does the orchestrator record now filed with М11: the tool that teaches best is not always the tool that ships best, and the documents say which is which.

## М10 — NodeVMS

Five lessons in which one box starts owning its own truth. `INSERT INTO cameras` causes a camera to start recording; `DELETE` stops it; killing the AppHost loses nothing but the open segment. Between the row and the pipeline there is only a loop the student wrote.

Its organising rule is that **desired state is persisted and actual state is derived** — persist the second and you have built a cache that lies. It is also where the process model from М9's decision record gets built: fifty GStreamer pipelines in one Python process, with the GIL boundary demonstrated rather than asserted.

- [Module design](./М10_NodeVMS/module-design.md) — lesson plan, the Python shard model, and what the operator is never asked to decide
- [`reference/shard-memory-probe.py`](./М10_NodeVMS/reference/shard-memory-probe.py) — measures what sharding actually saves, in PSS rather than RSS

## М11 — DomainVMS

Ten lessons, built on one decision taken up front: **a Node owns its own configuration.** A Node is not a server — it is a scheduler allocation with stable identity, so when a server dies the Node moves and its cameras go with it. Failover rewrites nothing, because ownership never changed.

**Part A** makes that true: a Nomad cluster, the Node as an allocation, and the lesson most courses skip — *what it takes for a Node's state to be there when it arrives on a new server*. Then a server is pulled off the wall, its old instance wakes up, and the archive is proved intact. **Part B** is the small residue: the three things a Node cannot know about itself — where a camera is, which Node should get a new one, and how to move one. That is a directory, it is allowed to be down, and it turns out not to be a database at all — a key-value entry per Node for the list, an object per Node for the restore point.

It states that decision rather than arriving at it, and then spends the module earning it — because the deciding fact is not obvious: **two writers to one video stream cannot be merged.** Nothing above can arbitrate after the fact, which is why this is the only module where a mistake corrupts customer footage rather than stopping a service.

It is built backwards from one demo. Four Nodes, two hundred cameras; pull the power on a server and watch Node 3 reappear elsewhere with its configuration intact. Then bring the dead server back and let its old instance of Node 3 try to keep writing. **The archive is intact, and the student can prove it.**

The answer is that fencing belongs at the archive rather than at the controller: the epoch is part of the segment path, so the stale instance cannot name the files it would otherwise corrupt. You cannot stop a zombie from writing — you can only make its writes harmless.

Because the Node is the writer, it is also the thing that has to be reachable and protected: Lesson 33 gives every Node↔directory stream **mTLS from the domain's own CA**, hand-provisioned and marked temporary. Certificates are issued *inside* the domain, so renewal never depends on the layer above — the same reason grants live in each Node and carry an expiry rather than being looked up.

- [Module design](./М11_DomainVMS/module-design.md) — the cluster, Nodes that outlive their servers, fencing, the directory above them, placement, shadow mode, and who may call the API
- [Kubernetes vs Nomad](./М11_DomainVMS/kubernetes-vs-nomad.md) — why the orchestrator is Nomad, what it cost, and why neither belongs on one box
- [Where the databases live](./М11_DomainVMS/where-the-database-lives.md) — five revisions ending with one database in the whole design, why a Node owns its configuration rather than caching someone else's, and the retention rule that protects customer footage

## М12 — OrchestratedVMS

Eleven lessons on what has to be true above any single domain — who a box is, who a person is, what a customer is entitled to, what the fleet consists of — and on the thing only this layer can supply: **capacity.** It runs in a cloud, public or the customer's own, and many domains operate through it.

Because it can hand out allocations, **a Node no longer has to run in the building the cameras are in.** Edge and cloud stop being two products and become a placement decision taken per site, on numbers the student computes: fifty cameras at 4 Mbps is 200 Mbps of sustained upstream, which most sites do not have — so recording stays at the edge, operation moves to the cloud, and *mixed* is what a real deployment looks like. The Node cannot tell the difference, and Lesson 37 proves it by diffing the artifacts.

That also closes the arc. **М8 rented a cloud VMS**; Kinesis held the configuration and the archive both. Eleven modules later the same product exists with nothing rented, and М9's hand-provisioned AWS credentials are retired by no longer being needed.

The demo: a box arrives in a carton, nobody types a secret into it, and minutes later it is recording — while a second site with no box at all runs on rented instances, indistinguishable in the console. Then the first site's uplink is cut for thirty days and it keeps working, because routine certificate issuance never leaves the site. The cloud site goes down for the duration, and the module says why that is the right outcome. Then a box is marked stolen and loses access on a schedule stated in advance.

It does not introduce the domain CA — М11 already built one. What this module supplies is its *authority*: an offline root, an intermediate delegated to each domain, and the swap performed under a running system. **A CA can be delegated; a vault cannot** — an intermediate is a bounded piece of the root handed down once a year, whereas a copy of a secret in every domain is N places to steal it from. That asymmetry is also the answer to the problem the course plan had flagged as having none. Unattended unsealing at 3am: **if the appliance needs a vault to boot, the vault is not allowed to be unavailable** — which contradicts the layer's own thesis. So the appliance holds certificates and does not run a vault.

- [Module design](./М12_OrchestratedVMS/module-design.md) — allocations and the edge/cloud/mixed triangle, secure introduction, the root above every domain, lifetimes against offline tolerance, inventory and version skew
- [Consul and OpenBao](./М12_OrchestratedVMS/consul-and-openbao.md) — two answers to mTLS, and why the product needs only one

## М13 — not yet started

Metrics and logs sized for a thin uplink: what to alarm on for a VMS, and why you cannot ship everything to a central Prometheus. Scope and sequencing in the [course plan](./COURSE-PLAN.md).

---

## How these lessons are written

Two conventions run through every lesson, and they are the reason the material is the length it is.

**Every step produces a result you can see.** A process you can signal, a container you can inspect, a pipeline whose output you can play, a page you can look at — not code taken on trust. Where a dependency could not be installed, the lesson still proves its logic some other way rather than asserting it.

**Each lesson replaces a stand-in from the one before.** `camera_sim.py` becomes the real pipeline, `filesink` becomes `kvssink`, fake clients become boto3, fixtures become real fragments. Nothing is ever more than one layer away from something already verified.

Decisions are recorded with their costs attached. Where a choice is weak — a missed acceptance criterion, a capability given up, a licence worth a lawyer's eye — the documents say so instead of quietly moving on.
