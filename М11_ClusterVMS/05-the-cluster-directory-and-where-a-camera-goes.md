# Lesson 5 — The Cluster Directory, and Where a Camera Goes

**Module:** ClusterVMS — a Node that outlives the server recording on it (Module 11)
**You will build:** *where is camera 7* answered from the cluster in one scan, and a placement function with property tests — including the one that fails the first time somebody adds a tidy-looking rebalance.
**Time:** ~120 minutes.

## Why this lesson exists

This is the lesson that costs almost nothing to build, because **you already built it in Lesson 2 and called it something else.** Every Node has a Variable listing its camera ids. Scan them and you have answered the question a directory exists to answer. Noticing that is the lesson's first move, and the rest of it is about what kind of thing that makes the directory — not a database, and strongly consistent in a way the level above this one can never be.

The second half is the other direction: a new camera arrives, and something has to decide which Node gets it. That decision is small, it is a pure function, and it has one rule that every clever improvement violates. The property test that catches the violation is the deliverable.

> **What you can verify without hardware.** All of the placement — [`reference/placement.py`](reference/placement.py) and [`reference/test_placement.py`](reference/test_placement.py) run with no cluster, no cameras, in milliseconds, and every output below is real. The directory scan needs the Lesson 1 cluster and a few Node Variables, which you have.

## Prerequisites

- **Lesson 2** — each Node's Variable, and the ACL that makes it one-writer-per-key.
- **Lesson 1** — `B` and `I` from the probe. Placement is by measured capacity, and this is where the measurement pays.
- **Lesson 4** — the epoch. Rebalancing is the one two-writer operation in this module, and it needs it.
- **М9 Lesson 9, Step 4** — *site is a first-class operator concept; server is not, and Node barely is.* Placement is the controller-owned row that lesson said a client may never write.

## Learning objectives

1. Answer *where is camera 7* by scanning Variables, and say why the answer is current.
2. Explain why the directory is not a database, using the three-stores rule.
3. Say what strong consistency inside a cluster gives you and why it stops at the cluster's edge.
4. Place a camera onto a Node by measured capacity under constraints.
5. State the stability rule, test it as a property, and show the rebalance that breaks it.
6. Argue why consistent hashing is the wrong tool here.

---

## Step 1 — You already have a directory

Lesson 2 gave every Node a Variable:

```
nodes/node-1   node=node-1  cameras=1,2,3,...,48   config=node-1/rev-207  revision=207
nodes/node-2   node=node-2  cameras=49,...,96      config=node-2/rev-88   revision=88
nodes/node-3   node=node-3  cameras=97,...,150     config=node-3/rev-812  revision=812
nodes/node-4   node=node-4  cameras=151,...,200    config=node-4/rev-31   revision=31
```

*Where is camera 7?*

```bash
nomad var list -out=json nodes/ | jq -r '.[] | .Path' | while read p; do
  nomad var get -out=json "$p" | jq -r 'select(.Items.cameras | split(",") | index("7")) | .Items.node'
done
```

```
node-1
```

Four Variables, read in milliseconds, cached by the console. **That is a directory**, and it has three properties worth naming because they decide what it is *not*:

- **Small.** Tens of entries, hundreds of bytes each. It fits Lesson 2's 64 KiB rule with room to spare.
- **One writer per key**, enforced by the ACL from Lesson 2: Node 3 writes `nodes/node-3` and nothing else can. There is no reconciliation because there is no contention.
- **Never queried by anything but an exact scan.** Nobody asks *which Nodes have more than forty cameras* of a directory; they ask *where is 7* and *what does Node 3 hold*.

Those are exactly the three properties that made the configuration store a **database** in М9 — large, contended, queried — with the sign flipped. The same rule that put configuration in Postgres puts the directory in Variables. Same rule, opposite answer, and that is how you know it is a rule rather than a habit.

## Step 2 — And it is strongly consistent

Say this out loud, because it is the single thing the level above this one cannot have:

> **Inside a cluster there is one Nomad raft, so *where is camera 7* has one answer and it is current.**

When Node 3 publishes a new camera list, that write is a raft commit. Every read after it — from any server, from the console, from a placement decision — sees it. There is no window in which two servers disagree about where camera 7 is, because there is only one log. Failover does not change the answer, because the answer is about the *Node*, and the Node did not change.

Across clusters there is no raft at all. Nomad regions are joined by gossip and share no state; a directory spanning two clusters is a directory of directories, each consistent within itself and none of them consistent with each other. [М12](../М12_DomainVMS/module-design.md) builds that, and М12 Lesson 1 there asks the student to name what was lost at the boundary. The answer is this step.

## Step 3 — Placement by measured capacity

A camera arrives. Something decides which Node records it, and М9 Lesson 9 already decided *who*: not the operator. The operator names a **site**; the controller turns that into a Node. Here is the controller's half.

Two inputs, both measured rather than guessed:

**Capacity**, in units the probe gave you. A Node's capacity is `(memory budget − B) / I` from Lesson 1, expressed as *cameras' worth of load*. A camera's load is its bitrate relative to the probe's reference stream: a 720p camera is `1.0`, a 4K stream at eight times the bitrate is `8.0`. Two hundred cameras is not two hundred units; it is whatever they add up to.

**Constraints**, as labels. A camera on VLAN `cctv-b` can only be recorded by a Node whose server reaches `cctv-b` — Lesson 2's `meta.vlans`, now on the camera as well:

```python
@dataclass(frozen=True)
class Camera:
    id: int
    load: float                       # in units of the probe's per-pipeline increment I
    labels: frozenset = frozenset()   # {"vlan:cctv-b"} — where it is reachable from

@dataclass
class Node:
    id: str
    capacity: float                   # (budget − B) / I
    labels: frozenset = frozenset()   # which VLANs this Node's server can reach
```

And the function, in full, because it is short and every line is a decision:

```python
def place(self, cam, cameras):
    """Place ONE new camera. Existing placements are never touched."""
    if cam.id in self.placed:
        return self.placed[cam.id]
    self.rev += 1
    best, best_free = None, 0.0
    for n in self.eligible(cam):                          # labels ⊆ node labels
        free = n.capacity - self.load_of(n.id, cameras)
        if free >= cam.load and free > best_free:
            best, best_free = n, free
    if best is None:
        return None                   # "the system is full" — never "Node 3 is full"
    why = f"most free capacity ({best_free:.1f}) among {len(self.eligible(cam))} eligible"
    self.placed[cam.id] = Placement(best.id, why, self.rev)
    return self.placed[cam.id]
```

Three things it does that a naive version would not:

- **It returns `None` rather than raising**, and the caller says *the system is full* — М9 Lesson 9's rule that capacity is expressed as the system, not a Node.
- **It stores a reason and a revision.** At 3am, *why is camera 812 on Node 3* is a row: `most free capacity (14.0) among 2 eligible`, at revision 4471. Not a hash to recompute.
- **It never touches an existing placement.** That is the next step.

## Step 4 — The stability rule, and its property test

> **Adding a Node moves nothing.**

A new server arrives; a new Node is created on it; the placement of every existing camera is unchanged. The new Node fills up with *new* cameras. This rule is not obvious and every optimising instinct violates it, so it is enforced as a property test rather than a code review comment:

```python
def test_adding_a_node_moves_nothing():
    for seed in range(30):
        r, nodes, cams = world(seed)
        p = Placer(nodes)
        for c in cams.values():
            p.place(c, cams)
        before = dict(p.placed)
        p.add_node(Node("node-9", capacity=80, labels=frozenset(VLANS)))
        assert p.placed == before                       # the rule
```

Why it matters: **a camera moving between Nodes is a stop and a start** — a pipeline torn down on one server and built on another, a gap in that camera's archive, an index split across two servers' disks, and two instances of a writer for the duration, which is Lesson 4's problem invited on purpose. A placement that shuffles cameras to be tidy turns a capacity expansion into two hundred small outages.

Now the test the lesson is named for. Somebody adds a rebalance. It re-places everything from scratch, biggest cameras first, because that packs better. It is well-intentioned, it passes every invariant — every camera on exactly one eligible Node, nothing over capacity — and:

```
   tidy rebalance moved 8 of 110 cameras — each one a stop and a start
test_the_tidy_rebalance_that_fails_first_time ... OK
```

Eight cameras moved for no reason anyone asked for. The invariants cannot catch it, because the result is *valid*; only the stability property can, because it compares against what was there before. **Store the placement; do not derive it.** A derived placement is one that can change whenever its inputs do, and its inputs include things like which server booted first.

The whole suite, real output:

```
test_adding_a_node_moves_nothing ... OK
   budget 5: 5 moves, e.g. (31, 'node-1', 'node-2'); reason: 'rebalance from node-1 (spread 100%)'
test_budgeted_rebalance_is_bounded_and_explainable ... OK
test_capacity_is_the_system_not_a_node ... OK
test_every_camera_on_exactly_one_eligible_node_or_refused ... OK
   tidy rebalance moved 8 of 110 cameras — each one a stop and a start
test_the_tidy_rebalance_that_fails_first_time ... OK

all 5 pass
```

## Step 5 — Rebalance, when you must

Sometimes cameras *should* move: a Node was retired and its cameras piled onto the others, and a fresh Node sits empty. The rule is not *never rebalance*; it is **rebalance is explicit, budgeted, observable, and interruptible** — never a side effect of something else.

```python
def rebalance(self, cameras, budget):
    """Move at most `budget` cameras from the most loaded Node to the least,
    only while that reduces the spread."""
    moves = []
    for _ in range(budget):
        loads = {n: self.load_of(n, cameras) / self.nodes[n].capacity for n in self.nodes}
        hi = max(loads, key=loads.get); lo = min(loads, key=loads.get)
        if loads[hi] - loads[lo] < 0.10:
            break                                  # within 10 %: leave it alone
        ...
        self.placed[c] = Placement(lo, f"rebalance from {hi} (spread {loads[hi]-loads[lo]:.0%})", self.rev)
        moves.append((c, hi, lo))
    return moves
```

- **Budgeted**: at most N moves per call, per minute. Five, not two hundred.
- **Observable**: each move is a row with a reason — `rebalance from node-1 (spread 100%)` — and a revision.
- **Interruptible**: stop calling it and it stops. There is no background thread deciding on its own.
- **A dead band**: within ten percent, nothing moves. Perfect balance is not a goal; not thrashing is.

And every move is **the one two-writer operation in this module**: the old Node must stop camera 7 and the new one must start it, and for the seconds in between both hold its configuration. That is exactly the situation Lesson 4 built the epoch for. A move is a failover you asked for, and it uses the same token.

## Step 6 — Why not consistent hashing

The question comes up, because consistent hashing is what a distributed-systems course teaches for *which node holds key K*. Three reasons it is the wrong tool here, and the third is the one that matters at 3am:

1. **Cameras are not uniform.** A hash ring assumes keys cost the same. A 4K stream at 8 Mbit/s beside a 720p one at 1 Mbit/s is eight keys pretending to be one; the ring balances counts, not load.
2. **Constraints break the ring.** A camera that may only land on Nodes reaching `cctv-b` cannot go where the hash says. Every constraint is an exception to the ring, and a ring made of exceptions is a lookup table with extra steps.
3. **It is not inspectable.** *"Why is camera 812 on Node 3?"* — because `hash(812) mod ring` said so. That is not an answer an operator can act on, and it is not one that survives adding a node (which is the whole point of the ring, and the exact thing the stability rule forbids).

A stored placement with a reason and a revision is a table you can read, sort, and explain. It costs one row per camera. That is not a cost.

## Step 7 — What this placement does not decide

One boundary, stated so the next module can cross it: **placement here chooses a Node. It does not choose a cluster**, because this cluster is the only one that exists so far. When a site has cameras on two networks in two buildings, and each building has its own cluster, something has to decide *which cluster* before this function decides *which Node* — and that decision uses different information (reachability from the site, not capacity of a Node) and lives at a different level. М12 Lesson 1 is where it arrives, and the student is asked to name why it cannot use this function.

**Deliverable:** *where is camera 7* answered from Variables in one scan on the bench; and `test_placement.py` passing — including the tidy-rebalance test, which you should first watch fail by removing the stability assertion and re-adding it.

---

## The module in Go, measured

М9 Lesson 9 made the language argument on one file — the reconciler — and promised that the rewrite touches only the actuator. A promise about one file is cheap. This module is five lessons of mechanisms, so the course checks the promise on all of them: [`clustervms-go/`](./clustervms-go/README.md) is `clustervms/` ported to Go, **with the Python suite's 29 tests ported alongside, unchanged in meaning**, and one test the Python version could not have written — a Go Node restoring a configuration a Python Node published. All thirty pass, and the CAS race runs on four real goroutines under the race detector rather than four threads under the GIL.

Then it puts the **whole Node** in each language at idle — fifty cameras restored from the directory, the epoch taken, every task running, a console listener, fakes for Nomad and Postgres — and reads PSS. And because a cluster controller is not a hot loop, it also times the six things this module actually does, with the same inputs:

| | Go | Python | |
|---|---|---|---|
| Node at idle, 50 cameras, every task running | **7.1 MB** | **28.5 MB** | 4.0× — the same ratio М9 saw on the reconciler alone |
| Static binary (x86-64 / arm64) | 6.6 / 6.2 MB | interpreter + wheels | |
| SigV4 sign, 64 kB object | 56 µs | 69 µs | 1.2× — SHA-256 is C in both |
| Issue an epoch by CAS | 0.84 µs | 1.75 µs | 2.1× |
| Directory scan, 1,000 Nodes, then *where is camera N* | 5.3 ms | 8.0 ms | 1.5× |
| Place 120 cameras on 4 Nodes with labels | 0.92 ms | 1.79 ms | 1.9× |
| Parse one segment path | 0.77 µs | 12.3 µs | 16× — the re-index sweep, the one place with a hundred thousand of anything |
| Encode + decode a 200-camera configuration | 0.57 ms | 0.72 ms | 1.3× |

Read the second half of the table before drawing the conclusion the first half invites. **The controller’s work runs within 2× in Python, and the hashing within 20 %**, because the hot part of each operation is already C. What Python cannot shed is the twenty megabytes it costs to be Python, and the interpreter-plus-wheels rootfs that М9’s bundle has to carry. So the win is exactly the one М9 named — memory per Node and the deployable artifact — and not the one people reach for, throughput. The port cost twice the lines (error returns and types), one dependency the standard library could not replace (a Postgres driver; Nomad and S3 needed none), and no redesign: every `>=`, every *object before the pointer*, every `TTL − margin` went across as it was. That last fact is the point of the exercise. The tests were written against a design, not a language, and the design is what survived.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| The scan finds camera 7 on two Nodes | Two Nodes' Variables both list it. The ACL is not enforcing one-writer-per-key, or a rebalance moved it without removing it from the source. The placement store is the truth; the Variables must follow it. |
| The scan finds camera 7 nowhere | It was placed and the Node has not published since. Lesson 3's *not yet replicated*, seen from the directory side. |
| Placement always picks the same Node | `free > best_free` with all Nodes equal picks the first. Fine — or add a tiebreak on Node id for determinism. Never randomness: the reason must be reproducible. |
| The stability test passes but cameras still move in production | Something other than `place()` writes the placement — a reconciler "correcting" it. Find it. Store the placement; do not derive it. |
| `rebalance` moves the same camera back and forth | No dead band, or the budget is larger than the imbalance. Ten percent and a small budget. |
| Capacity arithmetic places fifty cameras and the Node OOMs at forty | `B` and `I` measured on different hardware, or measured in RSS. Re-run the probe on the server that runs the Node. |

## Recap

- **The Node Variables from Lesson 2 already are the cluster directory.** *Where is camera 7* is a scan of tens of entries.
- It is a directory and not a database because it is small, one-writer-per-key, and only ever scanned — the three-stores rule with the sign flipped.
- **Inside a cluster the directory is strongly consistent, because it is one raft.** That property stops at the cluster's edge, and М12 has to live without it.
- Placement is by **measured** capacity (`(budget − B)/I`, loads in units of `I`) under label constraints, returns *the system is full*, and stores a reason and a revision.
- **Adding a Node moves nothing.** A move is a stop, a start, an archive split and a two-writer window; the property test catches the tidy rebalance that every invariant lets through.
- Rebalance is **explicit, budgeted, observable, interruptible**, with a dead band — and it is the one two-writer operation here, so it uses the epoch.
- Consistent hashing balances counts not load, cannot express constraints, and cannot explain itself.
- Placement chooses a Node. Choosing a cluster is the next level, and it uses different information.

## Exercises

1. Write the console query that joins the directory scan with М9's `camera_status` — *where is camera 7, and is it recording there* — and time it at 1,000 cameras. Then say whether the scan should be cached and for how long.
2. Add a `priority` to cameras and change `rebalance` to move low-priority cameras first. Then re-run the stability test and confirm it still passes — it should, and say why a rebalance policy cannot affect it.
3. Remove the dead band and run `rebalance` on a cluster with two Nodes of different capacity. Count how many calls before a camera moves back to where it started.
4. Implement consistent hashing for the same world and measure how many cameras move when a fifth Node is added. Then add one VLAN constraint and count how many keys become exceptions.
5. Write *where is camera 7* against two clusters' Variables, each read separately. Construct a sequence of one move and two reads that returns two different answers. That is М12's problem, met a lesson early.

## Where this is going

The module is complete. A Node runs wherever the scheduler puts it, moves when its server dies, restores itself from a Variable and an object, and cannot corrupt its own archive when two of it exist. The cluster knows where every camera is, consistently, and places new ones by measured capacity without moving old ones.

**And it is one cluster.** Every claim above holds inside one server room, on one raft. [**М12 — DomainVMS**](../М12_DomainVMS/module-design.md) adds the second cluster — another building, another city, a cloud region — and the first thing it has to say is what was lost: there is no raft spanning them, so the directory of directories cannot be consistent, and a Node still never crosses from one to the other. What it can do is know which cluster a site belongs to, sign for the whole estate, and stay up when the level above *it* is gone.
