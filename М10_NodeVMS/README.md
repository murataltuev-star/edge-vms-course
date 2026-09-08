# Module 10 — NodeVMS: One Node Learns What It Should Be

[Module 9](../М9_EdgeVMS/README.md) left you with an appliance that updates itself safely and keeps recording through an outage — and that has no idea what it is *supposed* to be doing. Every camera is configured by editing a file and restarting a container.

This module is where the box starts owning its own truth. Five lessons in which `INSERT INTO cameras` causes a camera to start recording, `DELETE` causes it to stop, and nothing sits in between but a loop you wrote.

The full design brief is in [`module-design.md`](module-design.md).

## The thesis

| | Holds | Written by | Survives |
|---|---|---|---|
| **Desired state** | what the operator asked for | the operator, through the API | reboots, OS updates, the AppHost dying |
| **Actual state** | what is running right now | the AppHost, by observation | **nothing** — it is re-derived every time |

> **Desired state is persisted. Actual state is derived.**

Persist the second and you have built a cache that lies: a green console over a box recording nothing. Lesson 21 makes you build that bug on purpose, because it is silent, plausible, and survives restarts.

**What this module builds is a Node**, and the capital letter matters from М11 onward. A Node is not a server — it is a VMS instance owning its own database, cameras and archive, and in М11 it becomes a scheduler allocation that moves between servers carrying all of it. Everything built here travels intact.

## Lessons

| # | Lesson | You'll be able to... |
|---|---|---|
| 20 | [The Database the Cloud VMS Didn't Need](20-the-database-the-cloud-vms-didnt-need.md) | Separate configuration from observation; write a `tstzrange` + GiST schema answering М8's timeline query directly; partition by time and demonstrate why `DELETE` is not retention; split operator-owned from controller-owned columns; write migrations safe to run unattended at boot. |
| 21 | [A Reconcile Loop with Nothing in It](21-a-reconcile-loop-with-nothing-in-it.md) | Write the loop against a `print()`; use `observed_revision >= revision` as the only test of "applied"; explain why polling is correctness and `NOTIFY` only latency; implement backoff **with jitter** and say what the jitter is for; build the lying-cache bug deliberately. |
| 22 | [Fifty Pipelines in One Process](22-fifty-pipelines-in-one-process.md) | Build pipelines from Python; explain where the work actually happens; cross the GIL boundary deliberately and watch the worker fall over; detect a stalled stream without touching a buffer; turn М9's spool into an archive. |
| 23 | [Failure Is the Feature](23-failure-is-the-feature.md) | Reproduce four failures on purpose and assert recovery from each; enforce retention under disk pressure without a scan; bound what a crash loses and prove it; state the fencing rule and the problem it stands in for. |
| 24 | [What the Console Shows, and What Python Stops Being Right For](24-the-console-and-the-rewrite.md) | Answer *is this camera recording?* in one query; keep positions apart from reasons; put a login in front of it; argue the production language split and identify what a rewrite would **not** touch. |

## The demo the module is built backwards from

```sql
INSERT INTO cameras (name, rtsp_url, site_id, enabled)
VALUES ('front-door', 'rtsp://10.0.0.41/stream1', 'store-14', true);
```

Within a few seconds, with nobody restarting anything: a pipeline is running, segments are landing on the data partition, and `SELECT name, phase, observed_revision FROM camera_status` says so. `UPDATE ... SET enabled = false` stops it. `systemctl kill apphost` loses nothing but the open segment.

If a lesson does not move that demo forward, it does not belong here.

## What you can verify without hardware

Better than М9's, because almost nothing here needs a bench.

**Runs anywhere:** the whole schema, the partitioning and retention arithmetic, the reconcile loop, the state machine, backoff and jitter, and the console query. The numbers printed in Lessons 20, 21 and 23 are real output — **PostgreSQL 16.13** and plain Python — not estimates.

**Needs GStreamer:** everything in Lesson 22, plus the stall test in Lesson 23. The module ships a probe script rather than asserting figures: `reference/shard-memory-probe.py` produces `B` and `I` on *your* hardware, which is what М11 Lesson 25 needs to size a shard.

Two corrections worth knowing before you start, both found by running the thing rather than reading about it:

- **PostgreSQL has no `DROP PARTITION` statement** — that is Oracle and MySQL. It is `ALTER TABLE … DETACH PARTITION` then `DROP TABLE`.
- **Partition pruning needs a predicate on the partition key.** `span && …` alone opens every partition's index; the bound on `lower(span)` is what prunes.

## The stand-ins, and where they get collected

The course names its temporary things where they appear rather than discovering them later. This module adds two of the five:

1. М9 Lesson 19 — AWS credentials in a file on the data partition
2. **Lesson 20 — the database password**
3. **Lesson 24 — one hand-provisioned operator account**
4. М12 Lesson 33 — a per-Node credential, and a self-signed domain CA

М12 collects them all — four replaced, one promoted. The `valid_until` column in Lesson 20's `grants` table is the mirror image: dead code here, present so that М12 *populates* rather than *migrates*.

## Where this goes

Everything in this module holds because there is exactly one box — one writer, one AppHost, a convention where М11 needs a fencing token, and one API surface to protect.

[**М11 — DomainVMS**](../М11_ClusterVMS/module-design.md) adds the second box. A Node becomes a scheduler allocation that moves between servers, and nothing built here changes — that is the design working. But two instances of one Node can briefly exist during a failover, and Lesson 23's one-line rule (*on restart, never resume the previous segment*) has to become an epoch the archive itself enforces.
