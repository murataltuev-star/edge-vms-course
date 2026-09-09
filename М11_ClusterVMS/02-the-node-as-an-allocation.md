# Lesson 2 — The Node as an Allocation

**Module:** ClusterVMS — a Node that outlives the server recording on it (Module 11)
**You will build:** М10's Node running as a Nomad job, behaving exactly as it did under Quadlet — and a Node identity that survives being moved to another server.
**Time:** ~120 minutes.

## Why this lesson exists

Lesson 1 built a cluster with nothing on it. This lesson puts a Node on it, and the first half is deliberately unexciting: a Quadlet unit and a Nomad task say the same things in different syntax, and a translation table gets you most of the way.

The second half is the reason the lesson exists. Once Nomad can move a Node between servers, the question *which Node is this?* stops having an obvious answer. The process that starts on Server B has an empty disk. It must know, before it does anything, that it is Node 3 — that camera 7 is its, that its configuration is object `rev-812`, that its last epoch was 5. Where does that knowledge come from, given that everything it knew is on a disk in a dead machine?

There is an answer that looks right and has a documented bug that corrupts archives, and there is the right one. The lesson has you meet both.

> **What you can verify without hardware.** The translation table and the jobspec are checkable with `nomad job validate` against the Lesson 1 cluster, and the Variables behaviour is what Lesson 4's [`reference/variables.py`](reference/variables.py) simulates from the API docs. Running the Node needs the bench and the М10 image. Rescheduling it needs a second server — which you have.

## Prerequisites

- **Lesson 1** — the cluster, ACLs enabled, `meta.vlans` set on each client.
- **М10 Lesson 5** — the Node as it stands: Postgres, AppHost, the console on 8080. [`nodevms/`](../М10_NodeVMS/nodevms/README.md) is what gets scheduled.
- **М9 Lesson 4** — Quadlet: `postgres.container` and `apphost.container` are the source text for the translation.
- A container image of the AppHost on every client (`localhost/nodevms-apphost:latest`, from `nodevms/Containerfile`).

## Learning objectives

1. Write a Nomad jobspec — `job` → `group` → `task` — and map each Quadlet key onto it.
2. Say which task drivers a VMS cares about, which are plugins, and what `exec2` demands of the OS.
3. Explain why the allocation index must never be a Node's identity, with the bug that proves it.
4. Give a Node its identity through a Nomad Variable, delivered by the scheduler, and reschedule it to prove the identity travelled.
5. Explain why configuration does **not** go in a Variable, using the maintainers' own reason.

---

## Step 1 — Quadlet to jobspec is a translation

Put the two side by side. Left, М10's `quadlet/apphost.container`; right, the same thing as a Nomad task:

| Quadlet (`.container`) | Nomad (`task`) | Note |
|---|---|---|
| `[Container] Image=` | `config { image = }` | same image, same registry |
| `Volume=/data/archive:/data/archive:z` | `config { volumes = [...] }` | same host path, same `:z` |
| `EnvironmentFile=/data/config/apphost.env` | `template { ... env = true }` | the environment now comes from the scheduler, not a file on the box |
| `Network=host` | `network_mode = "host"` | |
| `[Unit] Requires=postgres.service` | two tasks in one **group** | a group is co-scheduled onto one server, which is what `Requires` was for |
| `[Service] Restart=always` | `restart { }` and `reschedule { }` | Lesson 4: restart is the same server, reschedule is a different one |
| `[Install] WantedBy=` | `type = "service"` | a service job runs until stopped |
| `StopTimeout=20` | `kill_timeout = "20s"` | the open segment still finalizes |

Nothing in the left column lacks a right-hand side. That is the point of having built the Node on Quadlet first: the scheduler adds *where*, and changes nothing about *what*.

The whole job, from [`reference/node.nomad.hcl`](reference/node.nomad.hcl), trimmed to the parts this lesson is about:

```hcl
job "node-3" {
  datacenters = ["room-a"]
  type        = "service"

  group "node" {
    count = 1

    network {
      mode = "host"
      port "console" { static = 8080 }
    }

    task "postgres" {
      driver = "podman"
      config {
        image        = "docker.io/library/postgres:16"
        network_mode = "host"
        volumes      = ["/data/nodes/node-3/pg:/var/lib/postgresql/data:z"]
      }
      template {
        data        = <<-EOT
          POSTGRES_USER=nodevms
          POSTGRES_DB=nodevms
          POSTGRES_PASSWORD={{ with nomadVar "nodes/node-3/pg" }}{{ .password }}{{ end }}
        EOT
        destination = "secrets/pg.env"
        env         = true
      }
      resources { cpu = 500  memory = 512 }
    }

    task "apphost" {
      driver = "podman"
      config {
        image        = "localhost/nodevms-apphost:latest"
        network_mode = "host"
        volumes      = ["/data/nodes/node-3/archive:/data/archive:z",
                        "/data/nodes/node-3/config:/data/config:z"]
      }
      resources { cpu = 2000  memory = 2048 }
    }
  }
}
```

Two things to notice before running it.

**The job name is the Node's name.** `job "node-3"` — one job per Node, and the name is stable across every server it will ever run on. This is the first half of identity, and it is the easy half.

**The volumes are local, per Node, per server.** `/data/nodes/node-3/pg` on whichever server the job lands on. On a fresh server that directory is empty, Postgres initialises an empty database, М10's migrations run, and the Node has no cameras. **That is correct and expected**, and Lesson 3 is about what happens next. It is *not* a shared volume, and Lesson 3 shows why the obvious "fix" of making it one breaks failover.

`resources` are the numbers Lesson 1 measured: `B + 50 × I` for the AppHost, rounded up. A Nomad client will not place a task whose reservation does not fit, which is the first time the probe's numbers do work for you.

```bash
nomad job validate node.nomad.hcl
nomad job run node.nomad.hcl
nomad job status node-3
```

**Expected output** — one allocation, running, on one of your three clients:

```
Allocations
ID        Node ID   Task Group  Version  Desired  Status   Created  Modified
7f2a...   srv-b     node        0        run      running  30s ago  10s ago
```

Then the check that matters: the console answers on that server, exactly as it did under Quadlet.

```bash
curl -s http://10.0.0.12:8080/metrics | head -3
```

## Step 2 — The drivers, and the one that is not built in

Three task drivers matter to a VMS, and it is worth knowing which are plugins because an appliance image has to carry them:

| Driver | Runs | Built in? | A VMS wants it for |
|---|---|---|---|
| `podman` | OCI containers via Podman | **No** — separate plugin, installed in Lesson 1 | the same images and runtime as М9; nothing changes |
| `exec2` | a native process, sandboxed with **Landlock** and cgroups v2 | **No** — separate official plugin; beta in 1.8.0, GA in 1.9.0 | a worker that needs direct device access — a capture card, a GPU — without a container in the way |
| `virt` | a virtual machine | No — plugin | isolating a third-party analytics vendor's binary from the recorder |
| `docker`, `exec`, `raw_exec` | | yes | not on an appliance: `exec` has no Landlock, `raw_exec` has nothing |

`exec2` deserves the warning the module design gives it. It requires **Linux with the Landlock LSM and cgroups v2**, and that is a constraint on М9's base distribution, not a footnote: a kernel built without Landlock cannot run an `exec2` task at all, and the failure is a driver that never becomes healthy on a box you cannot visit. Check on the bench — `cat /sys/kernel/security/lsm` must list `landlock` — and put the check in the image build. Kubernetes has no equivalent to any of this; a native process with device access is simply not a thing it schedules, and that is one of the reasons [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md) came out the way it did.

For this module, `podman` is the driver throughout. The Node is already a pair of containers.

## Step 3 — Reschedule it, and watch it forget who it is

Now do the thing the cluster exists for. Drain the server the Node is on:

```bash
nomad node drain -enable -yes <srv-b's node id>
```

Nomad stops the allocation on `srv-b` and places a new one on `srv-a` or `srv-c`. `nomad job status node-3` shows the old allocation `complete` and a new one `running` elsewhere. **Failover, in one command.**

Now look at what arrived:

```bash
curl -s http://10.0.0.11:8080/metrics | grep nodevms_cameras
```

```
nodevms_cameras 0
```

Zero cameras. Postgres initialised on an empty directory, migrations ran, and the Node is a Node with no idea what it was. It is also, at this moment, not obviously Node 3 at all: nothing in the running process says so except the job name in its environment, and the job name is not enough to *find* its configuration, its camera list or its last epoch — all of which are on `srv-b`'s disk.

This is the lesson's actual subject. Before the Node can restore anything (Lesson 3), it must know **what it is**, and that knowledge cannot be on any disk.

## Step 4 — The wrong answer: the allocation index

Every allocation gets `NOMAD_ALLOC_INDEX` — an integer, `0` for a `count = 1` group, `0..n-1` for larger ones. It looks exactly like a Node number, and a student will reach for it: "Node 3 is allocation index 3 of job `nodes`, count 4".

**Do not.** Two reasons, one of them a documented bug.

The index is assigned per *allocation*, and Nomad's contract is that indices are unique among the *running* allocations of a group — not that a rescheduled allocation gets the same index the old one had, and not, as it turned out, even that two running allocations never share one. [Nomad issue #10727](https://github.com/hashicorp/nomad/issues/10727) reports **two simultaneously-running allocations with the same index**; it was accepted as a bug and later fixed. #4264 and #11628 are the same family. A fixed bug is still the wrong foundation: the index was designed as a *label* — fine for a metrics dimension, fine for a log prefix — and correctness of an archive was never something it was promised to carry.

Consider what "two allocations, one index" would have done here: two processes, both believing they are Node 3, both writing camera 7's archive. Lesson 4 is about exactly that failure, and it takes a fencing token to survive it. Building identity on a label that can be duplicated is building that failure in on purpose.

> **Rule: the allocation index is a label. Node identity is never derived from it.**

## Step 5 — The right answer: a Nomad Variable

Nomad Variables are an encrypted, namespaced, ACL'd key-value store held in the servers' raft and delivered to tasks through templates. They exist for precisely this: small, consistent facts a task needs that must survive the task moving.

Give Node 3 a Variable:

```bash
nomad var put nodes/node-3 node=node-3 cameras="" config="" revision=0
nomad var put nodes/node-3/pg password="$(openssl rand -hex 16)"
```

And deliver it to the AppHost task — the part of the jobspec Step 1 trimmed:

```hcl
    task "apphost" {
      # ...
      identity {
        env  = true      # NOMAD_TOKEN: the task's own workload identity
        file = true
      }
      template {
        data        = <<-EOT
          {{ with nomadVar "nodes/node-3" }}
          NODE_ID={{ .node }}
          CONFIG_OBJECT={{ .config }}
          CONFIG_REVISION={{ .revision }}
          CAMERA_IDS={{ .cameras }}
          {{ end }}
          DATABASE_URL=postgresql://nodevms:{{ with nomadVar "nodes/node-3/pg" }}{{ .password }}{{ end }}@127.0.0.1:5432/nodevms
        EOT
        destination = "local/node.env"
        env         = true
        change_mode = "restart"
      }
    }
```

Now reschedule again, and the process that starts on the new server has, in its environment before it runs a line of its own code: *I am Node 3; my configuration is this object at this revision; these are my camera ids.* Not from a disk. From the scheduler that placed it, out of a store replicated to every server in the cluster.

**Why this is the right mechanism and not merely a working one:**

- **It survives rescheduling by construction.** The Variable is in raft; the task reads it wherever it lands.
- **It is the second half of identity.** The job name says *which* Node; the Variable says *what that Node knows about itself*. Together they are enough to start the restore.
- **It is ACL'd, one writer per key.** [`reference/node-3-policy.hcl`](reference/node-3-policy.hcl) grants `nodes/node-3` and `nodes/node-3/*` to Node 3's workload identity and read-only on `nodes/*`. Bind it: `nomad acl policy apply -namespace default -job node-3 node-3 node-3-policy.hcl`. Node 3 cannot write `nodes/node-4`, and Lesson 5's directory rests on that. **The module design lists this as the open question to verify on the bench before building on it** — do so: try to write `nodes/node-4` with Node 3's token and confirm the 403.
- **The password is no longer in a file on the data partition.** М10 Lesson 1 called the column key's placement a debt and named the Variable as the payment. This is it, for the database password; the column key follows the same path.

## Step 6 — What does *not* go in a Variable

The instinct once Variables work is to put the configuration there too — the cameras, their URLs, their retention — and be done with Lesson 3 before it starts. Resist it, for a reason the maintainers state themselves.

Variables cap at **64 KiB per item** (originally 16 KiB, raised in 1.5.0), and they are capped at all because, in HashiCorp's words, the limit exists *"to reduce the potential performance impact of Variables on our raft store."* Read that as a design statement: raft is memory-resident and replicated to every server, so anything that grows is in the wrong place. A thousand cameras' settings do not fit in 64 KiB and should not be asked to; and a key-value store cannot answer *which cameras have retention over thirty days* anyway, which М10 Lesson 1 built a database to do.

What *does* fit is **the pointer**: a Node's identity, its camera ids, and *where its configuration object is and at which revision* — hundreds of bytes. That is what Step 5's Variable holds. The configuration itself goes somewhere built for large, rare, opaque blobs, and Lesson 3 chooses it.

Three stores, chosen by shape, and this is the rule the rest of the course stores things by:

| | Holds | Shape | Why not one of the others |
|---|---|---|---|
| **Postgres**, per Node | configuration, archive index, events | large, frequent, **queried** | the only one that can answer a question |
| **Nomad Variables**, per Node | identity, the epoch, the pointer | small, rare, **must be consistent** | raft is memory-resident and replicated everywhere |
| **Object storage**, per cluster | the published configuration — the restore point | large, rare, **never queried** | a blob nobody but its author parses |

> **Small and consistent goes in the scheduler's store. Large and queryable goes in a database. Large and opaque goes in an object store.**

Lesson 5 will point out that the Variables you just wrote — one per Node, each listing its cameras — already *are* the cluster's directory. Notice it now; the lesson collects on it.

## Step 7 — Placement constraints: cameras are not everywhere

One more thing before the Node is properly placed. Camera 7 is on VLAN `cctv-a`, reachable from `srv-a` and `srv-b` and not from `srv-c`, whose NICs are on `cctv-b`. Nomad does not know that; you tell it, using the `meta.vlans` each client declared in Lesson 1:

```hcl
  group "node" {
    constraint {
      attribute = "${meta.vlans}"
      operator  = "set_contains"
      value     = "cctv-a"
    }
```

Drain `srv-a` and `srv-b` at once and the job goes **pending** rather than landing on `srv-c` where it would record nothing. Pending is the right answer: a Node placed where it cannot reach its cameras is М10 Lesson 2's lying cache with a scheduler attached. The console shows the reason — `nomad job status` says which constraint filtered which node — and that reason is what М10 Lesson 5's "the system is full" message is built from.

Recordings, meanwhile, stay on the server that wrote them: `/data/nodes/node-3/archive` is a local path. **Do not put video bulk on replicated storage** — М9 Lesson 1 sized the data partition at hundreds of gigabytes per day; replicating that is a network you did not buy, and Lesson 3 shows it buys nothing for failover either.

**Deliverable:** М10's Node running as a Nomad job with the behaviour it had under Quadlet; then drained off its server and started on another with `NODE_ID`, `CONFIG_OBJECT` and `CAMERA_IDS` in its environment, read from a Variable nothing on either disk ever held.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `nomad job run` says `placement failure: constraint "${meta.vlans}" filtered N nodes` | No client declares that VLAN in its `meta`. Check `nomad node status -verbose` under *Meta*. |
| The template renders empty `NODE_ID=` | The Variable path is wrong, or the task's identity has no read on it. `nomad var get nodes/node-3` as the management token first; then bind the policy. |
| `permission denied` writing a Variable from inside the task | Expected for any path outside the policy — that is Step 5 working. For its own path, the policy is not bound to *this* job/group/task; re-run `nomad acl policy apply -job ...`. |
| Postgres task restarts in a loop with `initdb: directory exists but is not empty` | Two allocations of the same Node on the same server, or a leftover from a previous run. Local per-Node directories are per server; clean it. |
| The job runs but the AppHost cannot reach Postgres | `network_mode = "host"` missing on one of the two tasks. Both must be on the host network or both on a group network. |
| `exec2` task never starts | No Landlock: `cat /sys/kernel/security/lsm`. Or cgroups v1. Both are OS-image problems, not job problems. |
| After a reschedule, the Node reports zero cameras | **Correct for this lesson.** The restore is Lesson 3. |

## Recap

- A Quadlet unit and a Nomad task say the same things; the translation table has no empty cells. The scheduler adds *where* and changes nothing about *what*.
- `podman` and `exec2` are **plugins**, not built in. `exec2` needs Landlock and cgroups v2 — a constraint on the OS image.
- A rescheduled Node starts with an empty disk and must know **what it is** before it can restore anything.
- **The allocation index is a label**, with a documented duplicate-index bug. Never Node identity.
- **A Nomad Variable is Node identity**: raft-replicated, delivered by template, ACL'd one-writer-per-key, and it carries the *pointer* to configuration — hundreds of bytes — never the configuration itself.
- **64 KiB, "to reduce the potential performance impact on our raft store."** Small and consistent in Variables; large and queryable in Postgres; large and opaque in an object store.
- Constraints come from `meta.vlans`; a Node that cannot reach its cameras stays **pending**, on purpose.

## Exercises

1. Write the translation table for М10's `postgres.container` yourself, then diff it against the jobspec. Find the one Quadlet key with no Nomad equivalent and say what replaces it.
2. Write Node 3's token into a shell and try `nomad var put nodes/node-4 x=1` with it. Record the exact error. Then try `nodes/node-3/anything`. This is the open question from the module design — write down what you found and which Nomad version you found it on.
3. Set `count = 2` on the job by mistake, run it, and read `NOMAD_ALLOC_INDEX` in both allocations. Then read issue #10727 and explain what would happen to camera 7's archive if the index were its identity and the bug recurred.
4. Put 65 KiB into a Variable. Read the error. Then compute how many cameras' configuration would fit under the limit at your row size, and compare with the biggest site your product sells to.
5. Add a second constraint — `${attr.unique.hostname}` `!=` the server the Node is on — drain nothing, and watch what Nomad does with a constraint that can never be satisfied. Decide whether *pending forever* is the behaviour you want, and what the console should say.

## Where this is going

The Node has an identity that travels. It arrives on the new server knowing that it is Node 3 and that its configuration is object `rev-812` — and with an empty database.

**Lesson 3 is the restore**: what must travel and what must not, the shared-storage trap that looks like the grown-up answer and cannot fail over unattended, the six-step rehydration sequence, and the number this design costs — the recovery point objective — measured rather than promised.
