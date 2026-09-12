# Lesson 3 — Making a Node's State Outlive Its Server

**Module:** ClusterVMS — a Node that outlives the server recording on it (Module 11)
**You will build:** the restore — a Node that arrives on a new server and rebuilds itself from a Variable and an object store — and a measured recovery point objective.
**Time:** ~150 minutes.

## Why this lesson exists

Lesson 2 ended with a Node that knows it is Node 3 and has no cameras. This is the lesson most courses skip, because "it pulls its configuration back" sounds like a detail. It hides every interesting decision in the module: what has to travel, where it comes from, what order the writes go in so that a pointer never points at nothing, and — the part with a number attached — what the operator was told about an edit that did not survive.

There is also a trap in it that looks like the grown-up answer. Shared storage would make all of this go away: put the Node's disk on a SAN, and when the Node moves, its disk is already there. It is what a datacentre would do. It is the wrong answer for an appliance, for a reason worth reading in the bug tracker rather than taking on trust.

> **What you can verify without hardware.** The rehydration sequence, the publication order and the RPO measurement all run against fakes in [`reference/rehydrate.py`](reference/rehydrate.py), and every number printed below came out of it. The failover itself, and the CSI comparison, need the Lesson 1 cluster.

## Prerequisites

- **Lesson 2** — the Node as a job, its Variable, and the three-stores rule.
- **М9 Lesson 5** — what is in the database, and which of it is configuration. This lesson is about exactly that column split, one level up.
- **М9 Lesson 8** — the archive index is rebuildable from segments; events are observations.
- **М9 Lesson 4** — *delete on acknowledgement, never on send.* The same shape returns here as *acknowledge on local commit, show durability.*
- An S3-compatible object store reachable from every client — MinIO on the three servers is enough for the bench.

## Learning objectives

1. Separate what must travel from what must not, and justify each row.
2. Explain why shared storage cannot fail over unattended under Nomad, citing the open issue.
3. Walk the six-step rehydration sequence and say what each step depends on.
4. State the publication order and the reason for it.
5. Choose the acknowledgement rule and show what the operator sees.
6. Measure the RPO and move it.

---

## Step 1 — What must travel, and what must not

Node 3's data sits on Server A's disk. Nomad moves Node 3 to Server B. Go through the four things in its Postgres from М9 Lesson 5 and ask of each: *does the new instance need this to do its job?*

|                   | On the dead server | Comes with the Node?                                                                                                                                                          |
| ----------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Footage**       | stays              | **No — and it does not need to.** The past stays where it was written; a replacement records the future. Moving terabytes to move a process would be the tail wagging the dog |
| **Archive index** | stays              | **Rebuilt.** М9 Lesson 8 made it derivable from the segments on disk; when Server A returns, its index comes back with it                                                    |
| **Events**        | stays              | Expendable. They are observations, and М9 Lesson 5 said so                                                                                                                   |
| **Configuration** | stays              | **Must come. It is the source of truth, and losing it loses the Node**                                                                                                        |

So exactly one thing has to travel, and it is the smallest of the four: a few hundred rows of what the operator asked for. Everything else is either derivable or belongs where it is.

That table is the whole lesson in miniature. Most of the difficulty people expect from failover — *how do I move the data?* — dissolves once you notice that almost none of it should move.

## Step 2 — The trap: shared storage

The obvious way to make configuration "come with" the Node is to make its disk come with it. A **CSI volume** — a network block device the scheduler attaches to whichever server runs the task — does exactly that, and it comes with a bonus: the storage system attaches a volume to *one* host at a time, so the old instance physically cannot write. Fencing for free.

Read [Nomad issue #12118](https://github.com/hashicorp/nomad/issues/12118) before designing around it. Still open. The report: when a client holding a CSI volume dies, **the volume stays attached to the dead node**. The rescheduled allocation fails to place with *"volume is already published on another node"*; Nomad's volume watcher cannot force a detach it cannot confirm; and the documented workaround is a human detaching the volume at the storage provider's console.

Put that against the requirement from Lesson 1:

> *A single server failure must not stop recording for longer than __ minutes, **and must not require a person**.*

Shared storage buys fencing and **loses the automatic recovery it was adopted for**. The failure it exists to survive ends with someone logging into a SAN at three in the morning. For a datacentre with an operator on call, that may be acceptable. For a box in a server room nobody visits, it is the one property the design cannot give up.

| | **2a — shared storage** | **2b — local disk, configuration replicated** |
|---|---|---|
| Mechanism | the Node's database on a CSI volume | local disk; configuration published one way, pulled back on start |
| Fencing | the storage does it | needs a token issuer — Lesson 4 |
| **Automatic failover** | **No** (#12118) | **Yes** |
| Cost | a SAN or NAS, and a shared failure domain | a replication path and an issuer to build |
| Fits | a datacentre with an operator | **an appliance** |

The course builds 2b, and **the product offers only 2b**. 2a is kept here as the comparison you can explain — a design that looks like the grown-up answer and cannot meet the one requirement an appliance has — not as an option a datacentre customer can buy. One design, one failure story, one datasheet.

## Step 3 — The directory is each Node's off-box backup

The 2b mechanism in one sentence, and the mental model for everything after it:

> **A Node publishes its own configuration upward whenever it changes; the directory stores the latest revision per Node and never writes back.**

Four things follow, and each one is a property people usually have to fight for:

- **One-way, because a backup does not write back.** No merge, no conflict resolution, no election. The Node is the authority; the directory is a copy.
- **The directory may be down during normal operation.** You do not need a backup in order to *run*. Editing a camera works with the object store unreachable.
- **It is required to fail over**, because a failover is a restore.
- **It has a recovery point objective** — the publication interval, and therefore the most recent change an outage can lose — and that is a number the product states, not a surprise it discovers. Step 7 measures it.

"Upward" in this module means **the cluster's own object store** — three MinIO instances on the same three servers, or any S3-compatible endpoint inside the room. Nothing above the cluster has to exist for this to work, and that is the cleanest evidence the module offers that a cluster is a product on its own.

## Step 4 — The rehydration sequence

Walk it explicitly. Server A dies; Nomad places Node 3 on Server B:

```
1. empty Postgres; migrations run                       (М9 Lesson 5's runner, unattended)
2. read its own Nomad Variable — "I am Node 3; my configuration
   is object node-3/rev-812, and these are my camera ids"   (Lesson 2)
3. fetch that object from the CLUSTER's object store
4. restore it; check the revision against the Variable
5. request a new epoch                                   (Lesson 4)
6. begin recording into epoch-N+1
```

Each step depends on something specific, and naming the dependency is what makes the sequence teachable:

- **Step 2 is why identity cannot live on disk.** The disk is on Server A.
- **Step 3 is why something off-box must be reachable to fail over** — the object store — even though nothing off-box is needed to *run*. It lives in this cluster, on these servers.
- **Step 4 is where the RPO becomes visible.** The revision that comes back may be behind the one the operator last saw.
- **Step 5 is why the old instance's writes are harmless** even if Server A was only paused. Lesson 4.

Against the fakes, from `rehydrate.py` — real output:

```
saved · not yet replicated
published rev 1: object node-3/rev-1, Variable -> node-3/rev-1
saved · not yet replicated    <- rev 2, not yet published

-- Server A dies. Node 3 is rescheduled to Server B --
{"state": "restored", "revision": 1, "epoch": 1, "cameras": [7]}
camera 7 came back with retention_days=30 — the rev-2 edit the operator saw as
'not yet replicated' is gone. That is the RPO.
```

Read the last line twice. Revision 2 was committed locally, acknowledged to the operator, and did not survive. That is not a bug in the sequence; it is the cost of the availability 2b buys, and Steps 6 and 7 are about making it honest and making it small.

## Step 5 — The mechanism, and what not to build

**Not Postgres logical replication.** There is nothing at the directory to replicate *into* — no database, and М12 keeps it that way. The Node does the publishing itself, in code you can read:

```python
def publish(self):
    key = f"{self.node_id}/rev-{self.db.revision}"
    self.store.put(key, self.db.dump())                 # 1. the object, first
    items, idx = self.vars.get(f"nodes/{self.node_id}")
    self.vars.put(f"nodes/{self.node_id}", {            # 2. then the pointer, by CAS
        "node": self.node_id, "config": key,
        "revision": str(self.db.revision),
        "cameras": ",".join(str(c) for c in sorted(self.db.cameras))}, cas=idx)
```

**The order is the whole design.** Object first, Variable second. If the Node dies between the two, the Variable still points at `rev-811`, which exists; `rev-812` sits unreferenced in the store until the next publish overwrites the pointer. Reverse the order and a death between the two leaves a Variable pointing at an object that was never written — and step 3 of the restore fails on a Node that cannot come back until a human intervenes. `rehydrate.py` asserts it: *a Variable must never point at a missing object*.

Three consequences to build in:

- **Restore checks the revision.** Step 4 compares the object's revision against the Variable's. A mismatch means somebody wrote the Variable by hand, and the Node should refuse rather than guess.
- **A Node the directory has never seen comes up `unconfigured`.** Brand new, or its first publish never landed: there is nothing to restore, and it must **not invent a configuration**. It reports the state and waits for an operator, or for М12's enrollment.

  ```
  -- a Node the directory has never seen --
  {"state": "unconfigured"}
  ```

- **The archive index does not come back.** It is large and constantly written, so it is never published; only the configuration is. After a failover the Node knows *camera 7 has footage on Server A's storage* and nothing finer until Server A returns. Rebuild by scanning segments when it does — М9 Lesson 8's orphan sweep, run forwards — and know how long that takes at your scale: a million segments is minutes of `stat()`, not seconds.

## Step 6 — The acknowledgement problem

Here is the gap the backup framing exposes. **What is the operator told when they save a camera?**

| | Cost |
|---|---|
| Acknowledge only after publishing | Configuration edits now require the object store — the offline-edit property is destroyed |
| Acknowledge on local commit, say nothing | Silent data loss on failover. The operator was told *saved*; the change is gone |
| **Acknowledge on local commit, and show durability** | The operator sees *saved · not yet replicated* until it lands |

The third needs no new machinery. М9 already has `observed_revision >= revision` for the AppHost applying a change; the console shows the same shape for the directory receiving it. A camera row carries `revision`; the Node's Variable carries `revision` as last published; the difference is *how many edits are not yet safe*, and a Node that has been unable to publish for N minutes raises a condition — `replicated`, false, since 14:02 — on М9 Lesson 9's conditions axis.

This is М9 Lesson 4's rule in its second instance. There it was *delete on acknowledgement, never on send*. Here it is:

> **Never acknowledge a write whose durability you cannot vouch for — and never block the write on it either. Show the difference.**

## Step 7 — Measure the RPO, then move it

The publication interval is the RPO, and a number the product states should be a number you measured. `rehydrate.py` runs a thousand random failovers with edits arriving at twenty an hour and reports what did not survive:

```
-- RPO measured: 1000 random deaths, 20 edits/hour --
publish every 300s:  829 edits lost across 1000 failovers; worst window 299.9s
publish every  60s:  159 edits lost across 1000 failovers; worst window  59.5s
publish every  10s:   30 edits lost across 1000 failovers; worst window   9.7s
publish every   2s:    7 edits lost across 1000 failovers; worst window   1.6s
```

Two things to take from the table. The loss scales with the interval, as it must — but it never reaches zero, because *some* window always exists between commit and publish, and the only way to close it is to acknowledge after publishing, which Step 6 rejected. And the interval is not free: at two seconds, a thousand Nodes are writing an object and a Variable every two seconds each, and the Variable write is a raft commit replicated to three servers. **Publish on change, with a floor** — immediately when a row changes, no more often than every few seconds — and the RPO is a few seconds for a cost of nothing while nobody is editing.

Then do it on the bench, where the number is real: edit a camera, `nomad node drain` the server within the window, and read what came back.

**Deliverable:** two proofs. First, kill a Node and bring it back on another server with its configuration intact — `nodevms_cameras` on the new server equals the old. Then **measure the RPO**: edit a camera, kill the Node in the window before it publishes, show what the operator was told (*saved · not yet replicated*) and what survived; then shorten the interval and show the number move.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| The restored Node has cameras but the wrong retention on one | That is the RPO, working as designed and visible. Check whether the console said *not yet replicated* for that edit. |
| Step 3 fails: `NoSuchKey` for the object the Variable names | The publish order is reversed, or a hand-written Variable. Object first, Variable second, always. |
| Every reschedule comes up `unconfigured` | The Variable's `config` field is empty — the Node never published, usually because its token cannot write its own Variable. Lesson 2, Step 5. |
| The Node restores and then its old footage is invisible | Correct: the index did not travel. It says *footage on Server A, unavailable*. It comes back when Server A does. |
| Rebuilding the index takes an hour | A million `stat()` calls on a cold disk. Rebuild in the background, newest first, and serve the console from the partial index as it fills. |
| The CSI experiment leaves the volume attached to the dead client | Issue #12118. You have reproduced the reason this lesson builds 2b. Detach it in the storage console and write down how long that took. |
| Publishing every change floods the servers | No floor on the interval. Publish on change, no more often than every N seconds. |

## Recap

- **Exactly one thing must travel — configuration — and it is the smallest.** Footage stays, the index is rebuilt, events are expendable.
- **Shared storage cannot fail over unattended under Nomad** (#12118): the volume stays attached to the dead client and a human detaches it. It buys fencing and loses the property it was adopted for.
- **The directory is each Node's off-box backup**: one way, never writes back, not needed to run, required to fail over, and with an RPO the product states.
- Six steps; step 2 is why identity is not on disk, step 3 is why something off-box must be reachable, step 4 is where the RPO shows.
- **Object first, Variable second.** A pointer must never name an object that is not there.
- A Node the directory has never seen comes up **`unconfigured`** and invents nothing.
- **Acknowledge on local commit, and show durability** — *saved · not yet replicated* — never silent, never blocking.
- The RPO is measured: 829 lost edits per thousand failovers at 300 s, 7 at 2 s. Publish on change, with a floor.

## Exercises

1. Reverse the publish order in `rehydrate.py`, kill the Node between the two writes, and run the restore. Read the assertion that fires. Then write the operator-facing message you would show for a Node in that state, and decide who fixes it.
2. Implement *publish on change with a floor* and re-run the RPO measurement. Report loss and object-store write rate at 1, 5 and 30 seconds. Recommend one.
3. Set up MinIO on the three servers, then stop **two** of them and edit a camera. Confirm the edit is acknowledged, shows *not yet replicated*, and lands when MinIO returns. Then stop two and drain the Node's server — and watch failover wait. Write the datasheet sentence for that.
4. Build the `replicated` condition in `nodevms/` and the console row for it, with `since`. Then argue whether *not replicated for 10 minutes* should page anyone.
5. Time an index rebuild at 10,000, 100,000 and 1,000,000 segments on the bench disk. Plot it. Decide what the console shows at each point of the curve.

## Where this is going

A Node moves and comes back whole. Everything in this lesson assumed Server A was dead.

**Lesson 4 assumes it was not** — only partitioned, or paused, or slow — and there are now two instances of Node 3, both restored, both writing camera 7. Nothing can tell dead from partitioned from paused, and the design has to be correct without resolving it. The tool is a fencing token, and the first candidate students reach for is the one Kleppmann's argument is about.
