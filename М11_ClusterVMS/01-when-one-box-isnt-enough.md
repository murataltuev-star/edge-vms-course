# Lesson 1 — When One Box Isn't Enough

**Module:** ClusterVMS — a Node that outlives the server recording on it (Module 11)
**You will build:** a three-server Nomad cluster on the bench, a measured shard size, and a written argument for why *this* deployment needed a cluster and a single appliance never does.
**Time:** ~120 minutes.

## Why this lesson exists

Everything so far ran on one box, and one box is a perfectly good product. М9 made it update itself and М10 made it own its truth; if the box dies, the cameras stop, and for a shop with eight cameras that is the deal they bought.

This module is for the deployment where it is not the deal: two hundred cameras, footage that must survive a server dying, a site where a dark hour is a contract breach. That takes more than one server, and more than one server is not just "more servers" — it is a different kind of system, with a scheduler deciding where work runs and a set of new ways to be wrong.

The lesson has one argument to win before it builds anything: **on a single appliance, a scheduler is the wrong answer**, and students should leave able to make that case rather than repeat it. The second half builds the cluster the rest of the module runs on, and measures the one number every later placement decision depends on.

> **What you can verify without hardware.** The argument, the arithmetic and the shard-size measurement need nothing but Python and М10's probe. Building the cluster needs three VMs from the М9 bench (or three anything with a Linux kernel and Podman). Nothing in this lesson needs a camera.

## Prerequisites

- **М10 entire** — the Node: its Postgres, its AppHost, its cameras. This module runs several of them.
- **М9 Lesson 4** — Quadlet. Lesson 2 translates those units; this lesson explains why you keep them on one box.
- [**`apphost-and-process-model.md`**](../М9_EdgeVMS/apphost-and-process-model.md) — the process model at a thousand cameras, and why the orchestrator must not own camera lifecycle.
- Three bench VMs with Podman, each with a data partition; `nomad` **≥ 1.8.0** (target 1.10.x LTS — see the module design's version floor and [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md) for the licence).

## Learning objectives

1. Name the four things that force a second server, and say which one usually forces it first.
2. Argue — with a number — why an orchestrator on a single appliance costs operational surface and buys nothing.
3. Describe Nomad's model: servers, clients, raft per region, and why three or five and never two or four.
4. Bring up a cluster on the bench and read its state.
5. Measure a shard's per-process baseline and per-pipeline increment, report them as PSS, and derive a shard size.

---

## Step 1 — What actually forces a second server

Four things, and it is worth being precise because people reach for a cluster for the wrong one.

| Pressure | What runs out | Cluster or bigger box? |
|---|---|---|
| **Camera count** | CPU and memory for pipelines — М10 Lesson 3's `B + n × I` | A bigger box, for a long time. Fifty pipelines in one process is cheap; a server does several such shards |
| **Storage throughput** | disk write bandwidth, then disk *capacity* | More disks first. Two hundred cameras at 4 Mbit/s is 100 MB/s — one good disk — but 2 TB/day, which is where capacity beats bandwidth |
| **Retention** | disk capacity, linearly with days | More disks, or a second box when the chassis is full of them |
| **Availability** | **a server that must not be a single point of failure** | **This one.** No bigger box fixes it |

The first three are arithmetic and a purchase order. The fourth is the reason this module exists: a customer for whom *"the server died and the site was dark for four hours while somebody drove there"* is not acceptable. That requirement cannot be met by one box, however large, and meeting it means something has to notice the box died and start the work elsewhere. That something is a scheduler.

So write the requirement down before touching a scheduler, because the scheduler is the cost of that requirement and nothing else:

> *A single server failure must not stop recording for longer than __ minutes, and must not require a person.*

The blank is the product's **recovery time objective**, and Lesson 4 measures it.

## Step 2 — Why not on one box

The instinct after a few years near Kubernetes is that a scheduler is simply how you run things now. On an appliance it is worth resisting, and here is the case in three parts.

**There is nothing to schedule.** A scheduler's job is to decide *which server* runs a piece of work. With one server there is one answer. "Run N shards" on one box is a systemd template unit — `apphost@1.service`, `apphost@2.service` — which М9 Lesson 4 already gave you, with restart policy, ordering and the data-partition boundary. The scheduler would add a second supervision layer over the same processes and a second notion of "running".

**It has a cost the appliance cannot spare.** Nomad's production guidance sizes *servers* at 4–8+ cores and 16–32 GB+ of memory, and says nothing about single-node deployments at all — the shape is simply not one the tool is designed for. On an appliance, memory spent on a raft server is memory that was page cache for video.

**It has its own failure modes.** HashiCorp publishes a support note on **orphaned Podman containers after a Nomad agent restart** — the same shape of bug М8 Lesson 3 met with the Docker client, one layer up. On a box with a scheduler, an agent upgrade can leave a container running that nothing supervises. Under Quadlet that class of bug does not exist, because systemd *is* the supervisor and does not restart out from under itself.

Put the three together and the trade is: one more distributed system to operate, one more thing the OS image must carry, one more way to orphan a process — in exchange for a decision with one possible answer.

> **Rule: one server, no orchestrator. The scheduler arrives with the second server, because that is when there is first a decision to make.**

Write your version of this argument down as part of the deliverable. You will need it the first time somebody proposes "just run Nomad everywhere for consistency".

## Step 3 — Nomad's model, in the terms this module uses

Two roles, and the names matter because *client* does not mean what it means elsewhere:

| Role | Does | Count |
|---|---|---|
| **Server** | accepts jobs, holds cluster state in **raft**, decides placement | **three or five** per region |
| **Client** | registers its resources, runs the work it is given, reports back | every server that runs Nodes |

A **region** is one raft — one replicated log, one leader, one notion of what is true. Everything this module relies on for correctness (Lesson 2's Node identity, Lesson 4's epoch, Lesson 5's directory) lives in that raft, which is why this module's cluster and a Nomad region are the same thing.

**Three or five, never two or four.** Raft needs a majority to make progress. Three servers tolerate one failure; five tolerate two; two tolerate none — a two-server cluster is strictly worse than one server, because either one dying stops the other. Four tolerates one, like three, at greater cost. For a server room, three.

Note what the servers do *not* do: they do not run your Nodes. The servers decide; the clients execute. On a large cluster they are different machines. On the bench, and on a small site, the same three boxes do both — but the roles stay distinct in the configuration, and it is worth keeping them distinct in your head.

## Step 4 — Build the cluster

Three VMs from the М9 bench, or three of anything. Give them stable addresses — `10.0.0.11`, `.12`, `.13` below — and a data partition mounted at `/data`.

On each, install Nomad and the Podman driver. **Neither `podman` nor `exec2` is built into Nomad**; both are plugins downloaded into a directory the agent is told about:

```bash
# on each server
mkdir -p /data/nomad/plugins
# nomad binary: >= 1.8.0. Check:
nomad version
# nomad-driver-podman: a separate release, into the plugin dir
install -m 0755 nomad-driver-podman /data/nomad/plugins/
systemctl enable --now podman.socket          # the driver talks to Podman over its socket
```

Server configuration, [`reference/server.hcl`](reference/server.hcl):

```hcl
datacenter = "room-a"
data_dir   = "/data/nomad"

server {
  enabled          = true
  bootstrap_expect = 3
  server_join {
    retry_join = ["10.0.0.11", "10.0.0.12", "10.0.0.13"]
  }
}

acl {
  enabled = true
}
```

Client configuration, [`reference/client.hcl`](reference/client.hcl) — on the bench, on the same three boxes:

```hcl
datacenter = "room-a"
data_dir   = "/data/nomad"
plugin_dir = "/data/nomad/plugins"

client {
  enabled = true
  servers = ["10.0.0.11:4647", "10.0.0.12:4647", "10.0.0.13:4647"]
  meta {
    vlans = "cctv-a,cctv-b"
  }
  host_volume "nodes" {
    path = "/data/nodes"
  }
}

plugin "nomad-driver-podman" {
  config {
    socket_path = "unix:///run/podman/podman.sock"
  }
}
```

Three things in there are load-bearing for later lessons:

- **`data_dir` on `/data`.** Raft lives here. On an А/B appliance (М9) the rootfs slot is replaced by an update; the cluster's memory of every epoch ever issued must not be. This is М9 Lesson 4's boundary, applied to the scheduler.
- **`meta { vlans = ... }`.** What this server's network interfaces can reach. Lesson 2 turns it into a placement constraint, because cameras are not uniformly reachable from every server.
- **`acl { enabled = true }`.** Variables are ACL'd, and Lesson 2 depends on one Node being unable to write another Node's Variable. Enabling ACLs later, on a running cluster, is a migration; enable them on day one.

Start the agents (`nomad agent -config /etc/nomad.d`, or a unit that does), and read the cluster:

```bash
nomad server members
```

**Expected output** — three servers, one leader:

```
Name          Address     Port  Status  Leader  Raft Version  Build   Datacenter  Region
srv-a.global  10.0.0.11   4648  alive   true    3             1.10.x  room-a      global
srv-b.global  10.0.0.12   4648  alive   false   3             1.10.x  room-a      global
srv-c.global  10.0.0.13   4648  alive   false   3             1.10.x  room-a      global
```

```bash
nomad node status
```

Three clients, `ready`, each with the Podman driver healthy (`nomad node status -verbose <id>` shows the driver table). If a client shows `podman` as *undetected*, the plugin is not in `plugin_dir` or the socket is not enabled — both are in the troubleshooting table.

Then bootstrap the ACL system once and keep the management token somewhere that is not a lesson:

```bash
nomad acl bootstrap
```

## Step 5 — Break container-per-camera, and measure the shard

Before placing anything, decide what a unit of placement *is*. The obvious unit is one container per camera, and it is the wrong one; М9's process-model record argues it and this step measures it.

Run М10's probe against the real worker:

```bash
python3 ../М10_NodeVMS/reference/shard-memory-probe.py --pipelines 1
python3 ../М10_NodeVMS/reference/shard-memory-probe.py --pipelines 50
```

It reports two numbers, and they are the ones Lesson 5 sizes placement with:

- **`B`, the per-process baseline** — interpreter, PyGObject, GStreamer, before any camera exists.
- **`I`, the per-pipeline increment** — what each additional camera adds.

**Report PSS, not RSS.** Resident set size counts every page mapped by a process, including the pages of `libgstreamer` that fifty processes share. Sum RSS across fifty containers and you have counted that library fifty times. **Proportional set size** divides each shared page by the number of processes mapping it, so the sum across processes is the memory actually in use. The probe reads `/proc/<pid>/smaps_rollup` for exactly this reason; `ps` and `top` show RSS and will mislead you by a factor that grows with the number of processes.

Now the arithmetic that decides the unit of placement. With numbers of the order the probe produces on ordinary hardware (**check yours** — these are shapes, not claims):

```
B ≈ 60 MB per process        I ≈ 8 MB per pipeline

one container per camera:    200 × (B + I)   = 200 × 68 MB  ≈ 13.6 GB
four shards of fifty:          4 × (B + 50I) =   4 × 460 MB ≈  1.8 GB
```

Seven times the memory for the same two hundred cameras, all of it spent on two hundred copies of the same interpreter — and the per-camera version is *also* the one where a scheduler must make two hundred placement decisions and manage two hundred lifecycles, which is exactly what the process-model record said the orchestrator must not own.

So the unit Nomad places is a **Node**: one process, one shard of cameras, one database. Its size is:

```
cameras per shard  =  (memory budget − B) / I
```

with the budget chosen so that a whole server's shards leave room for page cache — video is a streaming write workload and starving the cache shows up as dropped segments before it shows up as an alarm. The honest cost of the shard, stated in М10 Lesson 3 and repeated here because it now decides a placement policy: **one segfault takes the whole shard.** Fifty cameras, not one. Bounded by shard size, by the scheduler restarting it in seconds, and by `splitmuxsink` losing only the open segment.

**Deliverable:** a three-server cluster with `nomad server members` showing a leader; `B` and `I` from *your* hardware and the shard size they imply; and one page arguing why this deployment needed a cluster and why the eight-camera shop from М9 must never get one.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `nomad server members` shows fewer than three, or no leader | `bootstrap_expect` must equal the number of servers, and all three must reach each other on 4647/4648. Check firewalls before anything else. |
| A client is `ready` but the job later fails with `driver podman not found` | The plugin binary is not in `plugin_dir`, or is not executable. `nomad node status -verbose` lists detected drivers. |
| Podman driver `undetected` | `podman.socket` is not enabled, or the socket path in `plugin` config is wrong. `curl --unix-socket /run/podman/podman.sock http://d/v4.0.0/libpod/info` must answer. |
| Two clusters instead of one | Two leaders elected because the servers could not see each other at bootstrap. Stop all three, wipe `data_dir/server`, start together. |
| The probe reports tiny per-pipeline numbers | You are reading RSS, or the pipelines never reached PLAYING. Confirm with `gst-inspect-1.0` that the elements exist and check the probe's state output. |
| Everything works and then the cluster forgets its jobs after an OS update | `data_dir` was in a rootfs slot. Step 4 — it belongs on `/data`. |

## Recap

- Four pressures force a second server: cameras, throughput, retention, availability. The first three are a purchase order; **availability is the one that needs a scheduler**, and the RTO you write down is what the scheduler is for.
- **One server, no orchestrator.** On a single box the scheduler has nothing to decide, costs memory the video wanted, and adds its own way to orphan a container.
- Nomad: servers decide, clients execute, one raft per region. **Three or five, never two or four.**
- Raft's `data_dir` belongs on the data partition; ACLs go on from day one; `meta.vlans` is a placement constraint waiting to happen.
- **PSS, not RSS.** Summing RSS across processes counts every shared library once per process.
- The unit of placement is a **Node**, not a camera: `(budget − B) / I` cameras per shard, and one segfault takes the shard.

## Exercises

1. Redo Step 5's arithmetic with your measured `B` and `I` for 200, 1,000 and 5,000 cameras, per-camera versus sharded. Find the camera count at which the difference stops mattering. (There is one, and it is small.)
2. Write the RTO sentence from Step 1 for three different customers — a shop, a warehouse, a prison — and say what each number implies for how many servers they buy.
3. Run a two-server cluster on purpose (`bootstrap_expect = 2`), stop one, and watch what happens to `nomad job status`. Then explain, in one sentence, why this is worse than one server.
4. Find the HashiCorp support note on orphaned Podman containers after an agent restart and reproduce it on the bench. Then reproduce the equivalent under Quadlet, and explain why you cannot.
5. Put `data_dir` in a rootfs slot, install an М9 bundle, and watch the cluster lose its raft. This is a five-minute demonstration of a mistake that costs a fleet.

## Where this is going

You have a cluster and a unit of placement. Nothing is placed yet.

**Lesson 2 makes М10's Node a Nomad job** — a translation from Quadlet, not a rewrite — and then meets the first hard question: when Nomad moves that job to a different server, *which Node is it?* The answer is not the allocation index, and getting it wrong corrupts an archive.
