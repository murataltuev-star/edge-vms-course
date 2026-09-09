# Lesson 28 — Failover, and the Two Instances of One Node

**Module:** ClusterVMS — a Node that outlives the server recording on it (Module 11)
**You will build:** a failover you can pull the power on, a fencing token that keeps two instances of one Node from corrupting an archive, and the two numbers this module exports.
**Time:** ~180 minutes.

## Why this lesson exists

Lesson 27's restore assumed Server A was dead. Here is the problem: **nothing can tell dead from partitioned from paused.** A server whose network cable was cut is still reaching its cameras and its disks and still writing camera 7's archive. A server under memory pressure can be stopped for thirty seconds and then continue as if nothing happened. Nomad sees a client that stopped heartbeating, waits, and starts a replacement. Now there are two instances of Node 3, both restored from the same object, both certain they own camera 7.

That is not a gap to close with a better heartbeat. It is the fundamental limitation of a distributed system, and the design has to be correct *without* resolving it. The tool is a fencing token, the place it is checked is the archive, and the first thing students reach for is a lock that reintroduces the problem while appearing to solve it.

This is also where the module's two numbers come from: how long a site can be dark, and how often something the design said was impossible has happened.

> **What you can verify without hardware.** The zombie, the fencing, the lease arithmetic and the issuer are all in `reference/` and every output below is real: [`fencing_demo.py`](reference/fencing_demo.py) runs two real processes with `kill -STOP` and `kill -CONT`; [`lease.py`](reference/lease.py) is the state machine on a monotonic clock; [`test_issuer.py`](reference/test_issuer.py) races four threads at a CAS store. The `disconnect` block, the drain and the power pull need the cluster.

## Prerequisites

- **Lesson 27** — the restore; step 5 of the sequence is this lesson.
- **М10 Lesson 23, Step 5** — *on restart, never resume the previous segment.* This lesson is the reason.
- **М8 Lessons 5–6** — `SIGSTOP`/`SIGCONT`, and what a process cannot know about itself.
- [Kleppmann, *How to do distributed locking*](https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html) — read it before Step 4. Twenty minutes, and the module's central argument is in it.

## Learning objectives

1. Distinguish restart from reschedule and say what the `disconnect` block's default gets wrong for a recorder.
2. Explain why two writers to one video stream cannot be reconciled, and what that forces.
3. Build a fencing token into the archive path and prove a resumed zombie is harmless.
4. Show why a lock is not a fencing token, with Nomad's own API.
5. Issue epochs with check-and-set, and say why a database sequence would be worse.
6. State the lease rule on a monotonic clock, and compute what the margins buy.
7. Export `node_failover_seconds` and `node_epoch_conflicts`, and say why the second is worth alarming on at zero.

---

## Step 1 — Restart versus reschedule

Two words Nomad keeps apart, and so should you:

| | Where | Governed by | Fixes |
|---|---|---|---|
| **Restart** | the same server | `restart { }` | a crashed process — М10 Lesson 22's segfault taking the shard |
| **Reschedule** | a different server | `reschedule { }` | a dead server, a full disk, a constraint no longer met |

Service jobs default to unlimited reschedule attempts with exponential delay, which is right for a recorder: a Node that cannot be placed should keep trying, because the alternative is a Node that gave up while the operator was asleep. The jobspec in `reference/node.nomad.hcl` sets a 15-second initial delay, doubling to two minutes.

Restart first, reschedule second: three restarts in ten minutes on the same server before Nomad decides the server is the problem. That order matters for the archive — a restart keeps the footage on the same disk; a reschedule leaves it behind.

## Step 2 — The `disconnect` block, and why its default is wrong

Here is what Nomad does by default when a client stops heartbeating: it marks the client's allocations **lost** and places replacements — *while the client, if it is merely partitioned, keeps running its tasks.* For a web service that is fine; two copies briefly serve requests. For a recorder it means a partitioned server keeps recording camera 7 while a replacement starts recording camera 7 somewhere else.

The `disconnect` block (Nomad ≥ 1.8.0; before that only `max_client_disconnect` and `stop_after_client_disconnect`, which 1.10 removed — the reason for the module's version floor) is where you decide what you want instead:

```hcl
  group "node" {
    disconnect {
      lost_after           = "2m"               # how long a silent client keeps its allocation
      replace              = true               # then place a replacement elsewhere
      stop_on_client_after = "2m"               # the partitioned client stops the old one itself
      reconcile            = "keep_replacement" # when the client returns, the new one wins
    }
```

The four `reconcile` strategies — `best_score`, `keep_original`, `keep_replacement`, `longest_running` — decide which instance survives when the partitioned client reconnects and Nomad discovers there are two. `keep_replacement` says the new one; the old one is stopped on reconnect.

Now the exercise that *is* the argument. Set `lost_after` to two minutes and ask: **for a recorder, is two servers recording the same camera for a minute better or worse than neither?**

- *Neither* means a gap in the archive of exactly `lost_after` plus placement time — a number you can state.
- *Both* means a minute of duplicate footage on two disks, and the old instance's minute is on a partitioned server whose footage may or may not ever be reachable again.

With fencing — Step 3 — *both* is safe, because the two instances cannot write into each other's files. Without fencing, *both* is the corruption Step 3 demonstrates. So the answer is: **both, but only once the archive enforces the epoch**, and `stop_on_client_after` bounds how long the old one keeps going. Set the three numbers so the partitioned instance stops itself *before* `lost_after` plus the lease margin expires on the replacement — Step 5 has the arithmetic.

## Step 3 — The zombie, and the fencing token in the path

Two writers to one video file do not merge. There is no last-write-wins for footage, no CRDT for a segment, no quorum read that returns the right frame. **The only correct outcome is that one writer's output is kept whole and the other's is discarded — and the decision about which cannot be made by either writer, because each believes it is the live one.**

Kleppmann's argument gives the shape: a lock service cannot stop a client whose lease expired during a pause from making unsafe writes, because the lock service has no visibility into what the client does afterwards. The fix is a **fencing token** — a number that increases each time the lease is granted — and the essential part is *where it is checked*: **the resource rejects the stale token**, not the lock service.

The VMS version is unusually clean, because the resource is a filesystem and the token can be part of the name:

```
archive/node-3/epoch-000005/cam-7/seg-00042.mkv   <- the old instance
archive/node-3/epoch-000006/cam-7/seg-00000.mkv   <- the live one
```

The old instance cannot corrupt the new one's segments because **it cannot name them.** It writes valid files into a directory the index no longer references, and retention deletes them. М10 Lesson 22 put `e1` into the path and said it did nothing yet. This is what it was for.

Run it, with two real processes:

```bash
python3 reference/fencing_demo.py
```

```
1. instance A (pid 1664) recording into epoch-000005: 12 segments
2. kill -STOP 1664   (Nomad cannot tell this from dead)
3. epoch 6 issued (ModifyIndex 1006); instance B (pid 1665) recording into epoch-000006: 12 segments indexed
4. kill -CONT 1664   (the zombie wakes and keeps writing)
5. after the zombie woke: A wrote 12 more segments into epoch-000005 — orphans nobody indexes;
   B's 24 segments in epoch-000006 untouched, contents verified

/tmp/archive-w70vlpkb/node-3/
  epoch-000005/cam-7/    24 files   <- retention deletes this
  epoch-000006/cam-7/    24 files   <- the index points here

You cannot stop a zombie from writing. You can only make its writes harmless.
```

And the same run with one shared path, which is what the archive looks like without the epoch:

```bash
python3 reference/fencing_demo.py --no-fencing
```

```
4. kill -CONT 1677   (the zombie wakes and keeps writing)
5. after the zombie woke, ONE shared path: 24 of the 24 segments the index names now contain
   instance A's data — silently. The index is a lie, the console will play epoch-5 footage
   under epoch-6 timestamps, and nothing raised an error.
```

Twenty-four of twenty-four. The zombie resumed its segment numbering where it left off, which is exactly where the live instance had started, and overwrote every file the index names. No error, no log line, and the customer finds out when they ask for footage from an evening that plays back as a different evening.

That second run is also the justification for a rule М10 introduced without one: **on restart, never resume the previous segment.** A resumed segment is a shared path by another name.

## Step 4 — Where the token comes from: the wrong answer first

The epoch must come from a **single issuer** and must **increase** — that is all fencing needs; it does not need to be dense. Two candidates in Nomad look right. One of them is the lock Kleppmann is warning about.

**Nomad Variable Locks.** Acquire, renew, release, a TTL between ten seconds and a day. It is the first thing a student reaches for, and it is exactly the wrong thing, for one reason the API makes visible: **the lock ID is an opaque UUID.** There is no monotonically increasing index. Two successive holders get two random strings, and nothing downstream can tell which is newer:

```
1. variable lock ............ two lock IDs, no order: 00d35f67 vs e14e55cc (a<b as strings — meaningless)
```

A holder that was paused, woke, and still holds a valid-looking ID cannot be distinguished by the archive from the live holder. The lock protects the *acquisition*; it does nothing for the *writes*, and the writes are the thing. This is the most instructive wrong answer in the module: it feels like the solution and it reintroduces the zombie.

**A Nomad Variable with check-and-set.** The Variables API takes `cas=<index>`, compared against the variable's raft-assigned `ModifyIndex`, and returns **409 Conflict** if anybody wrote in between:

```
GET  var nodes/node-3/epoch          → { epoch: 41, ModifyIndex: 8123 }
PUT  var nodes/node-3/epoch {42}  cas=8123
       200 → nobody else wrote      409 → re-read and retry
```

Atomic, single-issuer, monotonic, and it survives losing the server that issued the last epoch, because the index is raft. `reference/test_issuer.py` races four threads at it — real output:

```
2. CAS issuer ............... 4 threads x 50 = 200 epochs, all distinct, 1..200
3. stale cas ................ 409 (cas=1200 but ModifyIndex=1201)
4. ModifyIndex as epoch ..... [1202, 1203, 1204, 1205, 1206] — increase is all fencing needs
```

Line 4 is the simplification worth knowing: `ModifyIndex` is itself raft-assigned and monotonic, so *any* successful write yields a usable epoch. You do not need to maintain a counter; you need a write that succeeded, and its index.

**Why this beats a sequence in a database.** Postgres has `nextval()`. It works right up until the database is restored from a backup — at which point it reissues numbers already written into archive paths, and the recovery procedure has produced the silent corruption it existed to prevent. Nomad's raft is replicated to every server: you cannot lose the counter without losing the cluster, and if the cluster is gone there are no allocations to fail over. **And it adds no coupling** — failover already requires Nomad, because Nomad is what reschedules the allocation. This is a large part of why the level above this one ended up with no database at all.

The issuer, from `reference/variables.py`:

```python
def next_epoch(vars_, node, retries=10):
    path = f"nodes/{node}/epoch"
    for _ in range(retries):
        items, idx = vars_.get(path)
        current = int(items["epoch"]) if items else 0
        try:
            vars_.put(path, {"epoch": str(current + 1)}, cas=idx)
            return current + 1
        except Conflict:
            continue                        # 409: somebody else moved it; re-read
    raise RuntimeError("could not issue an epoch")
```

Step 5 of Lesson 27's sequence is a call to this, with the Node's own workload identity token, against its own Variable path — which the ACL from Lesson 26 says only Node 3 can write.

## Step 5 — Clocks, and what the margins buy

A fencing token makes the zombie's writes harmless. A **lease** is what makes the zombie *stop*, so that the window of two writers is short rather than indefinite — and the lease has a rule about time that Redlock famously got wrong.

> **Lease expiry must not depend on wall-clock time.** System clocks jump under NTP correction. Use a monotonic clock, and depend only on clock *rates*, never on two servers agreeing what time it is.

Two margins, on two sides:

```
holder:       may write while  now − last_renewal   <  TTL − margin     (stops early)
replacement:  may start when   now − last_heartbeat ≥  TTL + margin     (starts late)
```

The holder stopping is a **purely local decision** requiring no coordination — it reads its own monotonic clock and compares — which is precisely why it is the part that can be trusted. `reference/lease.py` simulates a partition (the holder keeps running, renewals fail) and a pause (`SIGSTOP`, `CLOCK_MONOTONIC` keeps counting), with the holder's clock running fast or slow:

```
scenario   ttl margin holder clock   replacement   overlap
partition   30      5        100%   at  45.0s     none
partition   30      0        100%   at  40.0s     none
partition   30      0         90%   at  40.0s     3.4s from t=40.0s  <-- two writers
partition   30      5         90%   at  45.0s     none
partition   30      5         70%   at  45.0s     0.8s from t=45.0s  <-- two writers
pause       30      5        100%   at  45.0s     none
pause       30      0         90%   at  40.0s     none
```

Read the third and fourth rows together. A holder whose clock runs ten percent slow thinks less time has passed than really has, and keeps writing past the moment the replacement starts — *unless* there is a margin. The margin buys tolerance for clock-rate error of `(TTL − m) / (TTL + m)`: with a thirty-second TTL and five-second margins, a holder running at 71% speed is still safe; at 70% it is not. That is a ridiculous amount of skew for a real clock, which is the point — **the margin turns "our clocks are roughly right" into an engineering bound.**

The pause rows are why `SIGSTOP` is the module's best teaching device: a stopped process's monotonic clock keeps counting, so on `SIGCONT` it already knows its lease is gone and stops before writing a byte. The archive epoch is still there for the case where it does not — the two mechanisms are belt and braces, and the braces are the ones with no failure mode.

Pick the three numbers — TTL, margin, `stop_on_client_after` — write them in the specification, and defend them. Longer TTL means slower failover; shorter means more false failovers on a jittery network. **Recovery time against the width of the two-writer window** is a real product trade, and it is the module's third open question.

## Step 6 — Pull the power

Now the demo the module was built backwards from. Four Nodes across the three servers, cameras on all of them, and:

```bash
# pull the power on the server running Node 3
```

On the bench: `pkill -9 qemu-system-x86_64` for that VM, or `bench/outage.sh power-cut` from М9. Then watch, with a stopwatch:

1. Nomad's heartbeat window expires → the client is *disconnected*.
2. `lost_after` elapses → the allocation is *lost* → Nomad places Node 3 elsewhere.
3. The restore (Lesson 27): migrations, Variable, object, revision check.
4. A new epoch by CAS.
5. Recording resumes into `epoch-000006`.

**`node_failover_seconds` is the time from step 0 to step 5**, and it is the product's **recovery time objective**. It is meaningless as an average. Report the **worst case**, because the customer's question is *how long could my site be dark*, and the answer to that is not a mean.

Then bring the server back and let the old instance wake up. It reads its Variable, sees an epoch it does not hold, and stops — or, if `reconcile = keep_replacement` reached it first, Nomad stopped it. Either way its post-outage segments are in `epoch-000005`, unindexed, and retention removes them. Count them: that is the second number.

**`node_epoch_conflicts`** counts how often a stale instance was fenced at the archive — a write attempted with an epoch the index no longer references. On a healthy cluster it is **zero, forever**. That makes it exactly the kind of counter people forget to alarm on, and exactly the one to alarm on: **a metric that is always zero is worth more than one that is always noisy**, because the day it moves, something the design said was impossible has happened.

## Step 7 — Planned failover

The same mechanism, with a human choosing the moment. М9 replaces a server's OS with a RAUC bundle and reboots it; on a cluster, that reboot is a failover you schedule:

```bash
nomad node drain -enable -deadline 5m <node id>     # Nodes move off, gracefully
rauc install update-2026.10-1.raucb && reboot         # М9 Lesson 17
nomad node drain -disable <node id>                   # the server takes work again
```

Drain is `disconnect` without the uncertainty: Nomad knows the server is leaving, stops the allocation cleanly (the open segment finalizes — М10's `SIGTERM` path), and places it elsewhere before the reboot. This is where М9's two update planes meet the scheduler: the OS plane updates a server, the scheduler keeps the Nodes running somewhere else while it does, and the health check from М9 Lesson 18 decides whether the server rejoins.

What does not fail over, planned or not: **the footage.** It stays on the drained server's disks and comes back with them.

**Deliverable:** pull the power on a server; report `node_failover_seconds` (worst of three runs) and how many seconds of camera 7 were lost. Then restore the server, let the old instance wake, and prove the archive is intact and its output orphaned — `node_epoch_conflicts` moved from zero, and every indexed segment verifies.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Failover takes ten minutes | `lost_after` plus Nomad's heartbeat grace plus the reschedule delay, all defaulted. Each is a number in the jobspec; Step 6 is where you tune them. |
| Two instances of Node 3 running, and both healthy in Nomad | The partitioned client reconnected before `stop_on_client_after`. Expected — and harmless *if* the epoch is in the path. Check `node_epoch_conflicts`. |
| Corrupted segments after a failover | The epoch is not in the path, or the old instance resumed a segment. Run `fencing_demo.py --no-fencing` and compare. |
| `next_epoch` loops forever on 409 | Another writer holds the same path — usually the same Node's old instance still alive. That is the design working; the loser should back off and, if it keeps losing, conclude it is the zombie. |
| The variable lock "works" in testing | It will. It fails only when a holder is paused past its TTL and resumes, which testing rarely does. `kill -STOP` it. |
| Epochs go backwards after restoring the servers from backup | You used a database sequence. Step 4. Nomad's raft is not restored from backups; it is replicated. |
| `node_epoch_conflicts` is always zero | Good. Alarm on it anyway. |

## Recap

- **Restart is the same server; reschedule is a different one.** Restart first, then reschedule, unlimited.
- The `disconnect` default replaces a partitioned client's allocations while it keeps running them. For a recorder that is two writers — acceptable *only* with fencing, and bounded by `stop_on_client_after`.
- **Two writers to one video stream cannot be merged**, so the epoch must be a fencing token from a single issuer, checked **at the archive**: the epoch in the path. The zombie cannot name the live files.
- Without it: 24 of 24 indexed segments overwritten, silently.
- **A Nomad variable lock is not a fencing token** — its ID is an opaque UUID. A **Variable with `cas`** is: atomic, monotonic, raft-replicated, 409 on conflict. A database sequence is worse, because a restored backup reissues numbers.
- **Leases on a monotonic clock, two margins.** The margin buys tolerance for clock-rate error of `(TTL − m)/(TTL + m)`; the holder stopping is local and needs no coordination.
- Planned failover is `nomad node drain` — М9's OS update meets the scheduler.
- Two numbers: **`node_failover_seconds`, worst case**, is the RTO; **`node_epoch_conflicts`** is zero forever and worth alarming on the day it is not.

## Exercises

1. Set `reconcile = "keep_original"` and repeat the power pull. Describe what happens to the replacement when the old server returns, and which epoch the archive ends up in. Decide which strategy a recorder wants and write the sentence for the specification.
2. Build the epoch issuer against a real Nomad Variable with the task's own token, then have two allocations of the same job race it. Confirm 200 distinct epochs, as the fake did.
3. Change `lease.py`'s TTL to ten seconds and find the margin at which a 5% slow clock is still safe. Then find the margin at which a 50% slow clock is safe, and say whether that margin is acceptable for failover time.
4. Take the fenced zombie's orphaned segments from Step 6 — they are real footage of the partition minute — and design an orphan sweep that *re-indexes* them under a "recorded by a fenced instance" flag instead of deleting them. Say who would want that and what it costs.
5. Reproduce the variable-lock failure: two processes, one lock, `kill -STOP` the holder past the TTL, `kill -CONT`. Show that both believe they hold it, and that nothing in the lock's API could have told the archive otherwise.

## Where this is going

A Node fails over, and two instances of it cannot hurt each other. Every Node in the cluster has a Variable saying which cameras it holds.

**Lesson 29 notices that those Variables are a directory** — *where is camera 7* answered in one scan, strongly consistent because it is one raft — and builds the other half: placing a new camera onto a Node by measured capacity, with the stability rule and the property test that catches the tidy-looking rebalance everybody adds first.
