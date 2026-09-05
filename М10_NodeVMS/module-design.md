# М10_NodeVMS — Module Design

**One box, one database, one loop: the row becomes a running pipeline.**

М8 built a VMS with no database — Kinesis held the configuration and the archive both. М9 made the box atomic and replaceable. This module gives the box its own truth and something that acts on it: five lessons in which `INSERT INTO cameras` causes a camera to start recording, and `DELETE` causes it to stop, with nothing in between but a loop the student wrote.

> **Scope note.** [`COURSE-PLAN.md`](../COURSE-PLAN.md) had М10 as Postgres alone, with the reconciliation loop deferred to М11. This module merges them. A database nothing acts on is not a working system, and the loop is far easier to teach on one box — where both ends are visible in one terminal — than across a cluster. М11 keeps what genuinely needs more than one node: placement, state that spans sites, and the product API.

---

## The thesis

Every layer so far has had a single source of truth that lived somewhere else. Now the box owns it, and owning truth means being able to answer one question:

| | Holds | Written by | Survives |
|---|---|---|---|
| **Desired state** | What the operator asked for | The operator, through the API | Reboots, OS updates, the AppHost dying |
| **Actual state** | What is running right now | The AppHost, by observation | Nothing — it is re-derived every time |

> **The rule that organises the whole module: desired state is persisted, actual state is derived.**

A student who persists actual state has built a cache that goes stale and lies. A student who forgets to persist desired state has built something that forgets its cameras on reboot. Both mistakes are worth making once, deliberately, in Lesson 26.

The convergence test is one comparison, and it is the same one at every layer above this: **applied means `observed_revision >= revision`.**

---

## The demo the module is built backwards from

```sql
INSERT INTO cameras (name, rtsp_url, site_id, enabled)
VALUES ('front-door', 'rtsp://10.0.0.41/stream1', 'store-14', true);
```

Within a few seconds, without anyone restarting anything: a pipeline is running, segments are landing on the data partition, and `SELECT name, phase, observed_revision FROM camera_status` says so. `UPDATE ... SET enabled = false` stops it. `systemctl kill apphost` loses nothing but the open segment, and the box converges again on restart.

If a lesson does not move that demo forward, it does not belong in this module.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Scope | **One node, end to end** | The loop is the lesson. Placement across nodes adds a second thing to get wrong before the first one works. |
| Worker model | **N pipelines in one Python process** | The conclusion of [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md), now built. Container-per-camera is М9's world and stops being right near fifty. |
| Actual state | **Derived, never persisted** | Kill the AppHost and it must rebuild its picture from Postgres plus observation. Anything it remembers across a restart is a bug. |
| Change notification | **Poll on a timer, `LISTEN/NOTIFY` for latency** | NOTIFY is not durable — a listener that was disconnected misses it forever. Notify for speed, poll for correctness. Teaching only NOTIFY produces a system that silently stops converging. |
| Node visibility | **Derived, never operator-set** | See below. The `cameras` table has no node column an operator can write. |
| Language | **Python for the course; Go + C++ for the product** | Python teaches the loop and makes the language boundary visible. The product splits it — Go for the controller, C++ for the media worker — and Lesson 29 says why that split costs almost nothing. |
| Database placement | **On the data partition, as a Quadlet unit** | М9's three-way boundary with consequences: `PGDATA` in a rootfs slot is destroyed by the next OS update. |
| DB credentials | **Hand-provisioned, marked temporary** | Follows the course's existing discipline. М12 replaces this, and the replacement is the lesson — but the temporariness is stated here, not discovered there. |

---

## Prerequisites

- **Lessons 5–6** — process supervision, signals, and the self-matching `pkill` bug. The AppHost is what `looper.py` grows into.
- **Lessons 9–10** — GStreamer pipelines and what each element does. Lesson 27 builds them from Python instead of a shell string.
- **Lesson 19** — Quadlet, and the OS/app/data boundary that decides where `PGDATA` goes.
- **Lesson 13** — configuration is read from the environment; credentials are never in the image.

New assumed knowledge: SQL at the level of `CREATE TABLE`, `JOIN` and `INSERT`. No prior Postgres administration.

---

## How the shard is actually organised in Python

The question this module has to answer honestly, because the intuition is that Python cannot do this and the intuition is wrong for a specific and teachable reason.

### Where the work happens

Once `pipeline.set_state(Gst.State.PLAYING)` returns, buffers move on **GStreamer's own native threads**, inside libgstreamer, in C. Python is not in that path. And PyGObject documents that "all PyGObject calls release the GIL during their execution and other Python threads can be executed during that time."

So a worker holding fifty recording pipelines is running fifty pipelines' worth of C and a trickle of Python: a bus message every few seconds, a state change when configuration changes, a status write every five seconds. The GIL is close to uninvolved.

### The seam where it goes wrong

PyGObject also documents that "signals get executed in the context they are emitted from." A callback attached to a signal or a pad probe therefore runs **in the streaming thread**, and to run Python there it must take the GIL.

Attach a `GST_PAD_PROBE_TYPE_BUFFER` probe to fifty cameras at 25 fps and that is **1,250 GIL acquisitions per second**, serialised through one lock, each one interpreting Python. That is how a Python media worker dies, and it has nothing to do with how many pipelines are in the process.

> **The rule: Python touches control, never data.**
>
> Banned in the recording path: `appsink`, `identity handoff`, buffer-level pad probes.
> Fine: bus messages, state changes, `splitmuxsink::format-location` (once per segment).

### Stall detection without touching a buffer

The obvious way to notice a camera that has stopped sending while its TCP socket stays open is to timestamp every buffer — which is exactly the banned thing. GStreamer already solves it in C: the **`watchdog`** element from `gst-plugins-bad` passes buffers through untouched and posts an **error on the bus** if none arrive within `timeout` milliseconds (default 1000; a few seconds is right for cameras).

```
rtspsrc ! rtph264depay ! h264parse ! watchdog timeout=8000 ! splitmuxsink
```

Zero Python in the data path, and the failure arrives on the bus the AppHost is already reading. This one element is worth a section of Lesson 27 on its own, because it is the model for the whole design: push the per-frame concern into C, keep Python at control rate.

### The event loop, and not having two of them

The AppHost speaks asyncio to Postgres and to its API. Running a `GLib.MainLoop` alongside it gives the process two schedulers and two notions of "later".

Don't. Each pipeline has its own bus; one asyncio task drains all of them with the **non-blocking** `bus.pop_filtered(...)` on a short tick:

```
Worker  (one process = one shard)
  asyncio tasks
    reconcile()    every 2 s, and on NOTIFY     desired (Postgres) vs actual (dict)
    pump_buses()   every 200 ms                 non-blocking pop on each pipeline's bus
    report()       every 5 s                    write observed_revision + conditions back
  CameraPipeline   per camera: state machine, backoff, current segment
                   IDLE -> STARTING -> RUNNING -> FAILED -> (backoff) -> STARTING
```

Fifty non-blocking pops every 200 ms costs nothing measurable. `bus.get_pollfd()` with `loop.add_reader()` is the tidier version and makes a good exercise; it is not worth the fragility as the default.

### What it costs

Each recording pipeline creates roughly three to five native threads, so fifty cameras is 150–250 threads in the process. Linux is fine with that, but it is a number to measure rather than assume — `reference/shard-memory-probe.py` from М9 is extended in Lesson 27 to report it alongside PSS.

And the honest cost: **one segfault takes the whole shard.** That is the price of sharding, paid in exchange for the per-process baseline. It is bounded by shard size, by systemd restarting the unit, and by `splitmuxsink` — a crash loses the open segment and nothing else.

### The rule is not really about the GIL

Worth stating explicitly in the lesson, because it is the part that survives a change of language. The constraint is not Python's lock. It is **crossing a language boundary once per frame.**

| | What a per-buffer callback costs | Verdict |
|---|---|---|
| **Python** | Acquire the GIL and interpret. Fifty cameras at 25 fps is 1,250 acquisitions per second through one lock | Fatal |
| **Go** | Enter the Go runtime from a C thread through cgo. Cheaper than Python, still real, and the rules on passing pointers make it awkward | Same discipline required |
| **C++** | Nothing. There is no boundary | The rule dissolves |

That last row is the real argument for C++ in the media worker, and it is a better one than "C++ is faster" — which, for a pipeline that never decodes, would barely be true.

---

## What the operator never decides

The instinct is right: an operator wants to assign cameras, not nodes. The useful part is knowing exactly where that stops being true.

**Node is derived, never chosen.** This has a schema consequence that Lesson 25 makes concrete: the `cameras` table has **no node column an operator can write**. Placement lives in a separate, controller-owned row with its own revision, and the API will not accept it from a client.

But the node is physical, and physics leaks in four places where hiding it would be a lie:

| Where it surfaces | What the operator actually needs to know |
|---|---|
| **Capacity** | "You cannot add camera 1001." Expressed as *the system is full*, not *node 3 is full* — but the number has to come from somewhere real. |
| **Storage locality** | Recordings live on the node that wrote them. If footage is not centralised, a dead node is unavailable footage, and that must be visible before the node dies. |
| **Failure grouping** | When a node fails, two hundred cameras go red. The console must show one cause, not two hundred faults — which means grouping by failure domain, which means naming the node *at that moment*. |
| **Reachability** | A camera on an isolated VLAN may be reachable from exactly one node. The operator expresses this as a **site**, and the controller turns it into a constraint. |

> **Site is a first-class operator concept. Node is not.** Conflating them is the common mistake: sites are where cameras are, nodes are how many boxes it took.

So: invisible in configuration, visible in diagnostics and capacity. The same relationship a filesystem has to disks — you do not assign files to spindles, and you certainly see the spindle when one fails.

---

## Lessons

### Lesson 25 — The database the cloud VMS didn't need

- Why М8's spec forbade a database, and why the answer flips on-prem: in the cloud KVS held the configuration; on a box, the box holds it
- Schema for cameras, streams, sites and retention policies
- **Operator-owned columns versus controller-owned columns.** `enabled`, `rtsp_url`, `retention_days`, `site_id` are written by people; `revision`, `assigned_worker`, `observed_revision`, `phase` are written by machines and never appear as form fields
- `revision` as a monotonic, controller-assigned integer per object — not a hash, not a timestamp
- Migrations as a shipped artifact, and the appliance constraint: they run at boot on a box nobody visits, so they must be idempotent and must never be able to leave it unbootable
- **`PGDATA` on the data partition.** Postgres as a Quadlet unit with its volume outside both rootfs slots — М9's boundary with teeth
- The database password is hand-provisioned and **marked temporary in the lesson text**; М12 replaces it

**Deliverable:** schema and migrations applied, Postgres surviving a simulated A/B update with its data intact.

---

### Lesson 26 — A reconcile loop with nothing in it

The `camera_sim.py` move, applied to control: build the loop before the thing it controls.

- Read desired from Postgres, compare against an in-memory dict of actual, log the difference. The actuator is a `print()`
- The vocabulary: desired, actual, converged, lagging, stalled — and `observed_revision >= revision` as the only test of "applied"
- **Poll versus `LISTEN/NOTIFY`.** Notify makes it fast; the timer makes it correct. A disconnected listener misses notifications permanently, so a system with only NOTIFY stops converging and does not say so
- asyncio structure: one task per *concern*, not one task per camera
- **Deliberate mistake, then fix:** persist actual state, restart the process, and watch it confidently report pipelines that are not running

**Deliverable:** an AppHost that converges a fake world, and passes a test that kills it mid-change.

---

### Lesson 27 — Fifty pipelines in one process

Swap the `print()` for GStreamer. This is the module's technical centre; see *How the shard is actually organised* above.

- Building pipelines from Python with PyGObject rather than a shell string
- **The GIL boundary**, demonstrated rather than asserted: add a buffer pad probe, watch the worker fall over, remove it
- Draining buses from asyncio without a `GLib.MainLoop`
- The per-camera state machine, and where backoff lives
- **The `watchdog` element** — stall detection in C, delivered on the bus
- Re-run the М9 probe against the real worker; add thread count to what it reports

**Deliverable:** insert a row, get a recording. Delete the row, the recording stops. Fifty cameras in one process, with measured memory and thread counts.

---

### Lesson 28 — Failure is the feature

Each failure mode reproduced on purpose, then handled.

- **Camera offline** → exponential backoff **with jitter**. Two hundred cameras reconnecting in lockstep after a switch reboot is a self-inflicted outage, and the jitter is the whole fix
- **Stalled stream, socket still open** → `watchdog` fires, that one pipeline restarts, the other forty-nine never notice
- **Disk full** → retention enforcement degrades by policy. The deletion loop must be conservative: never delete what it cannot prove is superseded
- **The AppHost dies** → systemd restarts it, state is re-derived, and the segment discipline bounds the loss
- **The fencing rule, introduced small:** on restart, never resume the previous segment — open a new one. Leases and epochs are М11's problem; the rule that makes them necessary lands here

**Deliverable:** a test suite that kills, fills, stalls and unplugs, and asserts convergence after each.

---

### Lesson 29 — What the console shows, and what Python stops being right for

- The joined view: desired and observed in one query, so "is this camera actually recording?" is not three round trips
- Status vocabulary for the UI: `converged`, `lagging`, `stalled`, `unreachable` as *positions*; licence, storage and reachability as **conditions** — reasons an object cannot converge, kept out of the phase enum
- **The node conversation**, from the section above: what the operator is asked, and the four places the node has to surface anyway
- **The rewrite sidebar.** Three things end Python's case for the product: the per-process baseline `B` is larger than a compiled worker's, one segfault takes the whole shard, and any requirement for per-frame work in Python is fatal by the table above
- **The split that follows from it.** **Go for the controller** — it is a gRPC-and-Postgres service, which is Go's centre of gravity, and its per-frame exposure is zero because the controller never touches a buffer. **C++ for the media worker** — GStreamer is a C library, so C++ calls it with no binding layer at all, and existing pipeline code can be reused rather than ported
- **What the rewrite does *not* touch**, which is the point of having written it in Python first: the schema, the reconcile loop, the state machine, the backoff policy and the desired/actual contract are all language-independent. Only the actuator changes. Building it in Python proved the design cheaply; it did not waste the work
- **Binding reality**, because it is easy to choose wrong here: `gstreamer-rs` is maintained by GStreamer's own developers and is the strongest non-C binding; `go-gst` is the live Go one; `gstreamermm` for C++ has been archived, so C++ means calling the C API directly — which is what C++ projects do anyway

**Deliverable:** the console view, and a written statement of every decision the operator is never asked to make.

---

## Verification plan

Better than М9's, because almost nothing here needs hardware.

**Track 1 — verified in the authoring sandbox.** Postgres runs in a container and needs no appliance; the schema, migrations, the reconcile loop and the state machine are all ordinary software. The loop is tested against a fake actuator exactly as Lessons 11–15 tested against fake AWS objects, which means convergence, backoff, restart and the deliberate mistakes are all provable here.

**Track 2 — needs a real bench.** Anything with GStreamer in it: the pipeline strings, the GIL demonstration, the `watchdog` timing, and the memory and thread measurements. The authoring sandbox has no GStreamer and the package mirrors are blocked, so Lesson 27's numbers come from the student's box, produced by a script that ships with the module rather than from figures asserted in the text.

Every lesson marks which of its claims were run and which are documentation-derived.

---

## Open questions

1. **Does the API belong here or in М11?** Lesson 29 builds a read view. A write API with authentication is arguably М11's, arguably М12's, and putting it here would make this a six-lesson module.
2. **How much retention policy is domain design rather than infrastructure?** The deletion loop is М10; schedules, per-camera overrides and legal-hold are product decisions that may deserve their own lesson in М11.
3. **Postgres in a container or on the host?** The module currently says Quadlet unit. On an appliance that nobody administers, a host package with systemd is a defensible alternative and the tradeoff is worth teaching either way.
4. **Does Lesson 28 need a real camera that misbehaves?** Cheap cameras stall in ways a simulator does not reproduce faithfully, and the module's most valuable failure mode is the hardest to fake.
5. **Lesson numbering** assumes М9 ends at 24.

---

## Sources

- [PyGObject — Threads & Concurrency](https://pygobject.gnome.org/guide/threading.html) — "all PyGObject calls release the GIL during their execution"; "signals get executed in the context they are emitted from"
- [GStreamer `watchdog` element](https://gstreamer.freedesktop.org/documentation/debugutilsbad/watchdog.html) — `timeout` in ms, default 1000, posts an error to the bus when no buffers arrive
- [`gstwatchdog.c`](https://github.com/GStreamer/gst-plugins-bad/blob/master/gst/debugutils/gstwatchdog.c) — the implementation, for the lesson that reads it
- [GStreamer pipeline manipulation](https://gstreamer.freedesktop.org/documentation/application-development/advanced/pipeline-manipulation.html?gi-language=python) — probes and their thread context
- [GStreamer bindings](https://gstreamer.freedesktop.org/bindings/) — which bindings are officially maintained
- [`go-gst`](https://github.com/go-gst/go-gst) — the live Go binding, successor to `tinyzimmer/go-gst`
- [`gstreamermm`](https://github.com/GNOME/gstreamermm) — archived, which is why C++ uses the C API directly
- [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) — the decision this module implements

*Written 4 September 2026.*
