# Lesson 2 — Shadow Mode: The Domain That Writes Nothing

**Module:** DomainVMS — the smallest layer above a set of clusters (Module 12)
**You will build:** a divergence report — six kinds of disagreement between what the directory says and what Nodes report, told apart by distance and time — and the written criterion for letting the domain write.
**Time:** ~90 minutes.

## Why this lesson exists

The course has a convention: the stand-in before the real thing. `camera_sim.py` before the pipeline, `filesink` before `kvssink`, a fake actuator before GStreamer. At the top layer the convention has teeth, because a domain that writes into thirty Nodes' worth of cluster state on its first day, on the strength of a model nobody has checked against reality, is how a fleet ends up with cameras nobody placed and placements nobody runs.

So the domain's first mode is one where it computes what it *would* do, observes what actually is, and prints the difference. The point is not caution for its own sake. It is that the difference is the design work: every camera running that the model does not describe is a gap in the model, and driving that number to zero is what "the domain is correct" means. You cannot skip it, only postpone it to a worse moment.

> **What you can verify without hardware.** All of it. `domain/shadow.py` takes the directory's view, the Nodes' reports and a clock, and returns findings; `tests/test_lesson2_shadow.py` is the lesson. Running it against live traffic — real Nodes, real revisions moving — is the bench's job and the reason shadow mode exists.

## Prerequisites

- **Lesson 1** — the directory of directories and cluster placement; this lesson compares them to reality.
- **М11 Lesson 4** — the epoch. One of the six kinds is a report under a superseded epoch, and it is the fencing rule catching a writer that should have stopped.
- **М9 Lesson 6** — `observed_revision` and the `>=` rule. Shadow mode reads the same numbers, one level up.
- **М9 Lesson 9** — positions and reasons. The report has kinds, not a health enum.

## Learning objectives

1. Compute a divergence report from placement, epochs, the directory revision and Node reports.
2. Name the six kinds and say which are faults, which are normal, and which are measurements.
3. Tell *slow* from *stuck* by distance and by time, and say why "diverged" is an alert you learn to ignore.
4. State the one number and defend its target.
5. Write the exit criterion for switching the domain into write mode.

---

## Step 1 — Ordering beats equality

Before the taxonomy, the token. Configuration replicates one way from each Node upward (М11 Lesson 3), and the domain must be able to say how far behind a Node is. The token could be an opaque value compared for equality, or an ordered revision. Ordering wins three ways:

1. **It expresses distance, not just difference.** "Diverged" is an alert you learn to ignore; "behind by four revisions for forty minutes" is an incident.
2. **It permits skip-ahead.** A Node offline across revisions 7, 8 and 9 converges straight to 9 without replaying. Edge links go down constantly; this is not an optimisation.
3. **It survives replay and reordering.** A late report carrying a lower revision is ignored rather than ambiguous.

The cost: you lose proof that one *precise* configuration was applied at one moment. If that must be auditable it belongs in an audit log, not in the convergence token. The report below uses `revision - observed_revision` everywhere, and never asks whether two blobs are equal.

## Step 2 — The six kinds

`Shadow.compare()` takes four things: `placed` (camera → the Node the directory says should run it, from М11's `placement/<camera>` cluster by cluster), `epochs` (node → the current epoch from `nodes/<node>/epoch`), the directory's `revision`, and the Nodes' reports — which since Lesson 3's heartbeat change are the snapshot objects each Node already publishes. It returns findings of six kinds:

| Kind | Meaning | Fault? |
|---|---|---|
| **lagging** | behind, within grace | No — normal |
| **stalled** | behind past grace, and progress static | **Yes** — the real "it didn't take effect" |
| **orphaned** | placed, and no Node claims it | Yes |
| **unmanaged** | a Node runs something the directory never placed | In shadow mode, **a measurement, not a fault** |
| **conflict** | two Nodes claim one camera, or the claimant is not the placee | Always — a fencing or placement failure |
| **stale_epoch** | a report under a superseded epoch | The fencing rule catching a writer that should have stopped |

One world, four Nodes, and everything goes wrong at once:

```
lagging=0  stalled=0  orphaned=1  unmanaged=1  conflict=1  stale_epoch=1  (directory revision 5)
  stale_epoch  camera=None node=node-3: reports under epoch 1, current is 2
  conflict     camera=3 node=None: claimed by ['node-2', 'node-4']
  unmanaged    camera=4 node=node-2: running, never placed
  orphaned     camera=9 node=node-3: placed, and no Node claims it
```

Read the first and last lines together, because they are one story. node-3 reports under epoch 1 while the cluster issued it epoch 2 — the old instance of node-3 is still alive somewhere and still talking. Its claims count for nothing (the code drops a stale-epoch report before counting claims), which is why camera 9, placed on node-3, is then *orphaned*: the only thing claiming it was a zombie. That is М11 Lesson 4's fence, seen from the domain: a writer that should have stopped, caught by the number in its own report.

The conflict is the other kind of wrong. Two live Nodes both say they run camera 3. Whatever caused it — a rebalance that moved a camera without removing it from the source, an ACL that let two Nodes write one key — it is never a tie to break and always a fault to raise.

## Step 3 — Slow versus stuck

The lagging/stalled boundary is the one that needs a clock, and it is worth watching move:

```
t=100  observed=5  revision=7   lagging     behind by 2 revision(s) for 0s
t=200  observed=5  revision=7   stalled     behind by 2 revision(s) for 100s
t=210  observed=6  revision=7   lagging     behind by 1 revision(s) for 0s
t=220  observed=7  revision=7   (clean)
```

`Shadow` remembers, per Node, the last `observed_revision` it saw and *when it changed*. Behind by two for a hundred seconds with no movement is *stalled* — the grace was sixty. The moment progress moves, the clock restarts and the Node is merely *lagging* again, even though it is still behind. Being behind is not the fault. Being behind and not moving is.

This is М9's `>=` rule with time added. A Node that reports `observed_revision = 5` against `revision = 7` is lagging; the same report ten minutes later, unchanged, means the reconcile loop on that Node is not converging, and *that* is what pages someone. "Diverged" would have fired at t=100 and been ignored by t=200.

## Step 4 — The one number

`unmanaged == 0`.

Anything running that the model does not describe is a gap in the model. In shadow mode it is not a fault — the domain has not placed anything yet, so on day one *everything* is unmanaged — and driving it to zero is the work: importing every camera the Nodes already run into the domain's placement, with a reason, until the report is clean. A domain switched to write mode with unmanaged cameras will, at its first rebalance, treat them as free space.

```python
def exit_criterion(rep, consecutive_clean, required=3):
    if rep.unmanaged:
        return False, f"unmanaged={rep.unmanaged}: the model does not describe everything that runs"
    if rep.faults:
        return False, f"{len(rep.faults)} fault(s) outstanding: ..."
    if consecutive_clean < required:
        return False, f"{consecutive_clean}/{required} consecutive clean reports"
    return True, "unmanaged == 0, no faults, stable across reports: the domain may write"
```

The criterion is code because a criterion that lives in someone's head is renegotiated at the moment it is inconvenient. Three consecutive clean reports is the default; the number is yours to defend.

**Deliverable:** a divergence report against your own cluster from Lesson 1 — with a camera you never placed, a zombie you resumed with `kill -CONT` (М11 Lesson 4), and a Node whose reconcile loop you paused — and the written exit criterion, met.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Every Node is *stalled* immediately | The grace is shorter than the Nodes' report interval; a Node cannot move `observed_revision` faster than it reports. Grace ≥ 2 × the heartbeat interval. |
| A Node flips lagging/stalled/lagging | Progress moves in bursts (a batch of edits, a long reconcile). Widen the grace or look at why one pass takes that long — М9 Lesson 7's shard size. |
| `unmanaged` never reaches zero | Something adds cameras at a Node's console without going through placement. In shadow mode that is the measurement working: find the path and route it. |
| `stale_epoch` on a Node that is healthy | The `epochs` map is stale — the domain read `nodes/<node>/epoch` before the Node's last restart. Re-read; if it persists, the Node is not reading its own epoch after the prologue. |
| `orphaned` for a camera you know is recording | Its Node's report was dropped as stale-epoch (see above) or the Node has not heartbeaten since. Both are worth knowing. |

## Recap

- The domain's first mode computes, observes, and prints the difference. It writes nothing.
- Six kinds: lagging (normal), stalled (fault), orphaned (fault), unmanaged (a measurement here), conflict (always a fault), stale_epoch (the fence, seen from above).
- Slow versus stuck is distance *and* time; "diverged" is neither.
- The one number is `unmanaged == 0`, and reaching it is the design work, not a formality.
- The exit criterion is code, met before the domain writes.

## Exercises

1. Add a seventh kind, *duplicate placement* — a camera placed on two Nodes in the directory itself — and say whether it can happen if `placement/<camera>` is written by CAS. If it cannot, delete the kind and write down why.
2. Replace the per-Node progress memory with a per-camera one. What changes in the report for a Node that converges forty-nine cameras and stalls on one?
3. Run the report with grace = 0. Count the stalled findings against the Lesson 1 cluster and say what the number measures.
4. Write the shadow report as a Prometheus exposition: one gauge per kind, labelled by cluster. Which kind deserves an alert with no threshold?

## Where this is going

The domain can now see. [**Lesson 3**](03-the-api-and-what-it-refuses.md) lets people see it: the camera list assembled from the same snapshots this lesson read, the write API that forwards to the owning Node and refuses to set placement, and — because someone has to serve browsers and it must not be a Node — the console and the live gateway.
