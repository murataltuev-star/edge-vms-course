# М9_EdgeVMS — Module Design

**From cloud service to shipped appliance, and from one appliance to a fleet.**

Modules 1–7 built a VMS that runs when you type `make serve`. This module turns it into something a customer plugs in and forgets about — and then into something you operate a hundred of.

> **Revision note.** This module was first designed around k3s and Rancher Fleet. It was rewritten around **Nomad** after a review of the alternatives; the reasoning is recorded in [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md). The switch improved the module's continuity — Podman is no longer discarded halfway through — at the cost of GitOps maturity and licence cleanliness, both of which are now taught rather than hidden.

---

## The thesis

There are **two independent update planes** in any real edge product, and the whole module is built to make that distinction land:

| Plane | Question it answers | Tool here | Changes when |
|---|---|---|---|
| **Below** | What operating system is this box running? | RAUC | You ship a new appliance image |
| **Above** | What workload is running on it? | Podman/Quadlet, then Nomad | You ship a new app version |

Students routinely conflate these, then build systems where a config change requires an OS flash, or where an OS update silently destroys recordings. The module's spine is: **the OS is atomic and replaceable; the app is a container; the data is neither and must outlive both.**

The Nomad rewrite sharpens this. Podman is the container runtime in Part A *and* in Part B — Nomad's Podman task driver means the scheduler is added **above** what students already know, rather than replacing it. One runtime, one mental model, from single appliance to fleet.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Target platform | x86-64, UEFI + GRUB | How a VMS appliance actually ships. Testable end-to-end in QEMU before touching metal. |
| Payload | M1–7 app, **KVS retained** | The edge box becomes a managed gateway that still publishes to AWS. No media-layer rewrite; deployment and lifecycle are the new skill. |
| Orchestrator | **Nomad** | Schedules non-container workloads, keeps Podman as the runtime, far smaller operational surface. See the decision record. |
| Multi-site model | Nomad **regions + federation**, packaged with **Nomad Pack** | Native to Nomad. The push-vs-pull tradeoff against Fleet is taught explicitly, not glossed. |
| Bundle delivery | Plain HTTP(S) | `rauc install https://…` keeps the focus on the update mechanism. **Eclipse hawkBit** is the production answer and now matters more than it did: it restores pull-based OS updates, partly offsetting the reconciliation lost with Fleet. Candidate for promotion out of a footnote. |

---

## Prerequisites

Carries forward from the existing course:

- **Lessons 7–8** — containers, images vs. containers, Dockerfile, why credentials are passed by name and never baked in
- **Lesson 13** — `config.py` reads settings but never credentials; boto3 finds them in the environment. That discipline is what makes an appliance image shippable
- **Lessons 5–6** — process supervision and signals. systemd replaces `looper.py`'s hand-rolled supervision here, and Nomad later replaces systemd's role for multi-node work. The comparison is worth making explicit at both steps

New assumed knowledge: none.

---

## Part A — The Appliance (single server)

*Unchanged by the Nomad decision. Nothing here depends on the orchestrator.*

### Lesson 16 — The appliance problem, and your test bench

**Why:** A box in a customer's server room has three enemies your laptop doesn't: power loss halfway through an update, a bad update with nobody on site to fix it, and an operator with no Linux skills. `apt upgrade` fails all three.

- Mutable vs. atomic systems; why "update in place" is the thing being replaced
- A/B (dual-slot) design: two complete root filesystems, one active, one being written
- The partition layout, and why the data partition is the most important decision in it
- Build the QEMU x86-64 UEFI bench and boot it

```
/dev/sda1   ESP        vfat    ~512M   GRUB + grubenv      ← persistent, NOT redundant
/dev/sda2   rootfs.0   ext4    ~8G     bootname=A
/dev/sda3   rootfs.1   ext4    ~8G     bootname=B
/dev/sda4   data       ext4    rest    config, container storage, recordings
```

**Deliverable:** a VM that boots, with both slots present and manually selectable.

### Lesson 17 — RAUC: slots, bundles, and signatures

- `system.conf`: `[system]` compatible/bootloader, `[keyring]`, `[slot.rootfs.N]` with `device`, `type`, `bootname`
- `bootloader=grub` and why `grubenv` must live outside both rootfs slots
- The GRUB contract: `ORDER`, `<bootname>_OK`, `<bootname>_TRY`
- Bundles are **signed**; the device verifies against its keyring. Build a real CA and signing cert with `openssl`, sign a bundle, and watch an unsigned or wrong-key bundle get refused
- `rauc status`, `rauc install <bundle>`, reboot into the other slot

**Deliverable:** an update installed into the inactive slot and booted.

### Lesson 18 — Rollback that actually works

The payoff lesson, and the one that must be *seen*, not described.

- Boot counters and the `_TRY` / `_OK` handshake
- `rauc status mark-good`, and what should mark it in a real system (a health check, not a timer)
- **Deliberately ship a broken update** — one whose service never comes up — and watch the box come back on the old slot by itself
- Pull the power mid-install and confirm the active slot is untouched

**Deliverable:** a written record of three induced failures and the observed recovery.

### Lesson 19 — Podman, Quadlet, and the three-way boundary

- Quadlet: `.container`, `.volume`, `.network`, `.pod` files that systemd turns into services
- Unit locations: `/etc/containers/systemd/` for root, `~/.config/containers/systemd/` for rootless
- Key fields: `Image`, `Exec`, `Volume`, `PublishPort`, `AutoUpdate`
- Run the M1–7 VMS as containers: the FastAPI server, the edge agent, the web assets
- **The critical detail:** Podman's storage must be redirected to the data partition. Container images and volumes in the rootfs slot get destroyed by the next OS update, and both slots must stay identical
- Where AWS credentials live on an appliance: not in the image (both slots ship identical), but provisioned at commissioning onto the data partition — Lesson 13's rule, now with teeth
- `podman-auto-update`, and why an appliance might *not* want it

**Deliverable:** the VMS running under systemd on the appliance, surviving reboot, publishing to KVS.

**Sidebar (context, not taught):** RAUC is not the only atomic-update approach, and for a product shipping on x86-64 UEFI it may not be the best one — **bootc** ships the OS itself as an OCI image, through the same registry and signing chain as the containers above. RAUC is kept here because A/B slots are legible, its signature verification is unconditional, and its bootloader coverage means these lessons port to ARM. Alternatives compared in [`rauc-alternatives.md`](rauc-alternatives.md).

**Second sidebar (setting up Lesson 20):** one container per camera is correct at this scale and stops being correct somewhere near fifty. Say so here rather than letting students generalise the pattern silently; Lesson 20 breaks it deliberately. Reasoning in [`apphost-and-process-model.md`](apphost-and-process-model.md).

---

## Part B — Many Servers, Many Sites

### Lesson 20 — When one box isn't enough

Opens with the honest argument, including the counter-argument.

- What actually forces a second server: camera count, storage throughput, retention, availability
- **Why an orchestrator is the wrong answer for a single appliance** — Part A's stack is genuinely better there. Scheduling earns its complexity only at multi-node
- Nomad's model: **servers** accept jobs and place work, **clients** register and execute it. Servers in a region form one raft consensus group and elect a leader; three or five servers per region
- Regions may span multiple datacenters
- Build a cluster: three servers, two clients
- **Break container-per-camera on purpose.** Run [`reference/shard-memory-probe.py`](reference/shard-memory-probe.py) to measure the per-process baseline against the per-pipeline increment, and derive the shard size from the curve. Teach PSS versus RSS here — summing RSS across processes double-counts every shared library page and produces the wrong conclusion
- The consequence for the rest of the course: the orchestrator schedules *workers*, and a domain controller (М11) assigns *cameras* to them. Camera lifecycle must not require a healthy control plane

**Deliverable:** a working cluster, a measured shard size, and a written justification for why this deployment needed one.

### Lesson 21 — The VMS as a Nomad job

The lesson where Part A pays off rather than being discarded.

- Jobspec structure: `job` → `group` → `task`, written in HCL
- **The Podman task driver** — the same images and the same runtime as Lesson 19. Translating a Quadlet unit into a Nomad task is a genuine mapping, not a rewrite
- The other drivers, and why they matter to a VMS: `exec2` for a native process needing device access, `virt` (beta) for a VM. Kubernetes cannot do this at all — it is the strongest argument for Nomad in a video product
- Storage reality: recordings stay local or on NAS. **Do not put video bulk on replicated storage.** Replicate metadata; let footage be local and let the archive be KVS
- Placement: cameras are not uniformly reachable from every client node

**Deliverable:** the VMS running as a Nomad job with the same behaviour as Part A.

### Lesson 22 — Many sites: regions and federation

- Regions are **fully independent** — they share no jobs, clients or state, and nothing replicates between them
- They are loosely coupled by a **gossip protocol**, so a job can be submitted to any region, or any region's state queried, transparently; requests are forwarded to the right regional servers
- Why "independent regions, loosely coupled" suits camera sites better than one stretched cluster: a site that loses its uplink keeps recording
- Namespaces and node pools for separating tenants and hardware classes

**Deliverable:** two federated regions, each running the VMS, both reachable from one CLI.

### Lesson 23 — Packaging, delivery, and the honest GitOps gap

- **Nomad Pack**: templating and packaging with remote registries, deploying multiple resources together — the closest thing to Helm in this ecosystem
- Per-site variation: site name, camera list, retention, stream names, from one pack with per-site variables
- CI-driven delivery: a pipeline runs `nomad-pack run` against each region

**And then the part most courses would skip.** The Kubernetes world has pull-based reconcilers — Fleet, Argo, Flux — where an agent at each site pulls desired state from Git and heals drift locally. Nomad has no first-class equivalent; the pattern here is **push-based**, and your pipeline has to reach each region.

For edge sites on flaky links, that is a real downgrade. Students should be able to state the difference, say which failure modes it introduces, and describe what they would build to close it. Teaching the gap is more useful than pretending the two ecosystems are equivalent.

**Deliverable:** one pack, three simulated sites, per-site differences — plus a written analysis of what breaks when a site is offline for a day.

---

## Synthesis

### Lesson 24 — Two update planes, and the licence

- RAUC updates the OS *underneath* Nomad; Nomad updates workloads *on top of it*. Same box, two planes, different cadences
- Draining a client node before an OS update, and returning it to service
- What breaks if you conflate them
- **Reading the licence you just built on.** Nomad Community Edition is BUSL, Licensor IBM. Production use is granted provided the work isn't offered to third parties on a hosted or embedded basis to compete with IBM's paid versions; the Change Date is four years per version, converting to MPL 2.0. Students shipping commercial products need to be able to read a grant like this and know when to ask a lawyer. Full analysis in the decision record
- Acceptance criteria for the module, in the style of the spec's section 10

---

## Verification plan — and an honest limitation

Modules 1–15 held to a rule: every step shows a real, observed result, and nothing ships unverified. **Module 9 cannot fully meet that bar in the authoring sandbox.** `qemu`, `podman`, `rauc` and `nomad` are all absent, package downloads are blocked (403), and there is no `/dev/kvm`.

The Nomad switch makes this slightly worse in one respect: Kubernetes manifests are YAML, which is trivially validated here, whereas jobspec is HCL and no parser is available. Jobspec validation moves to Track 2, where `nomad job validate` is the real command anyway.

**Track 1 — verified here.** Everything that is a file, a schema, a signature, or logic:

- The **RAUC signing chain** with real `openssl` — build a CA, sign, verify, and prove a wrong-key bundle fails. *Already demonstrated: correct bundle accepted, rogue-CA bundle rejected, tampered bundle rejected.*
- `system.conf` and bundle manifest structure, parsed and checked
- Quadlet unit files checked with `systemd-analyze verify` (systemd 255 is present)
- Partition arithmetic and any shell logic
- Per-site variable rendering logic simulated in the fake-object style of Lessons 11–15

**Track 2 — verified on real hardware, by you or a student.** Boot, rollback, cluster formation, federation, `nomad job validate`, Nomad Pack rendering. Each lesson carries an explicit *expected output* block so a deviation is recognizable rather than mysterious.

Every lesson will mark which claims are run-here versus documentation-derived.

---

## Open questions

1. **Lesson numbering.** This assumes М9 continues at 16, i.e. М8 doesn't add numbered lessons.
2. **Commissioning.** Does the appliance need a first-boot setup flow (network, credentials, stream name)? Adds roughly a lesson.
3. **Hardware acceleration.** GPU/codec passthrough into containers — in scope, or explicitly deferred? Note Nomad's `exec2` driver changes this calculus favourably.
4. **The licence.** If the vendor will not ship BUSL code in a product, that is a reason to teach Kubernetes instead regardless of technical fit — a course should not train people on a stack their company can't use.

---

## Appendix — Porting to ARM and other platforms

The module targets x86-64 UEFI, but the stack is portable. Of the nine lessons, **only Lesson 17 is platform-bound.**

### Layer by layer

| Layer | Portable? | What changes |
|---|---|---|
| **RAUC** | Yes — ARM is its native territory | The bootloader backend and its tooling |
| **Podman / Quadlet** | Yes, arch-agnostic | Images must be built multi-arch |
| **Nomad** | Yes — single Go binary, linux arm64 builds published | Nothing structural |

### The bootloader is the only real swap

RAUC supports `barebox`, `u-boot`, `grub`, `efi` and `custom` backends, selected by one key in `system.conf`.

| Platform | Backend | Boot-state tool |
|---|---|---|
| x86-64 or ARM server, UEFI | `grub` / `efi` | `grub-editenv` / `efibootmgr` |
| ARM SBC (most) | `u-boot` | `fw_setenv` / `fw_printenv` |
| ARM SBC (barebox) | `barebox` | `barebox-state` |

What differs when porting: the boot mechanism, where boot state is stored, partition naming (stable paths versus raw device names), and kernel command-line handling.

What does **not** differ: slots, `bootname`, the `ORDER` / `_OK` / `_TRY` handshake, `mark-good`, atomic install, and rollback semantics. Lessons 16, 18 and 19 port unchanged.

### Multi-arch images

Every image needs an arm64 build. The awkward one is **`kvssink`** — a compiled C++ SDK, not a pip install.

- AWS documents a native Raspberry Pi build, so ARM is genuinely supported
- Cross-compiling to aarch64 has known friction (open issue in the SDK repo)
- Plan on **native ARM builders**; `qemu-user` emulation works but is slow for a build this size
- Build per architecture, publish one multi-arch manifest

### Hardware video acceleration — the cost that actually bites

This does not port. Each vendor has its own stack:

| Platform | GStreamer plugin | Notes |
|---|---|---|
| Intel / general GPU | `va` (gst-plugins-bad) — `vah264dec`, `vapostproc` | Supersedes the older `vaapi` plugin |
| NVIDIA | `nvcodec` (gst-plugins-bad) | NVDEC/NVENC, Fermi and newer |
| ARM SoCs | `v4l2` (gst-plugins-good) | Kernel API exposing the SoC's codecs |
| AMD | `amfcodec` | |
| Apple | `applemedia` | |

**The mitigation worth teaching:** GStreamer selects decoders by *rank*, and `GST_PLUGIN_FEATURE_RANK` re-ranks them at runtime. A portable appliance can ship **one pipeline description plus one environment variable per SoC**, instead of per-platform pipeline code.

**Why this doesn't bite the course yet:** the M1–7 pipeline is pure pass-through — `filesrc ! qtdemux ! h264parse ! kvssink`, no decode, no encode. Add real cameras with transcoding, or analytics needing decoded frames, and this becomes the dominant porting cost — larger than RAUC by a wide margin.

### Storage cautions on ARM

- For a VMS, continuous recording destroys SD and eMMC flash. Attach real storage
- Nomad's raft consensus is also write-sensitive; keep server state off flash
- **Avoid 32-bit ARM.** A 32-bit address space and video buffers are a bad pairing. arm64 only

### Suggested treatment

Keep x86-64 / UEFI as the taught target. Ship this appendix as student-facing reading after Lesson 17, with one exercise: *name the three things that change if this appliance ships on an ARM SoC instead, and the three that don't.*

---

## Sources

- [RAUC integration](https://rauc.readthedocs.io/en/latest/integration.html) — bootloader backends, slot config, GRUB/EFI, commands
- [Podman Quadlet (`podman-systemd.unit`)](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) — unit types, paths, fields, auto-update
- [Nomad architecture](https://developer.hashicorp.com/nomad/docs/architecture) — servers/clients, raft, regions, gossip federation
- [Nomad task drivers](https://developer.hashicorp.com/nomad/plugins/drivers) — pluggable drivers, Podman, exec2, virt
- [Nomad Pack](https://developer.hashicorp.com/nomad/tools/nomad-pack) — templating, registries, Helm comparison
- [Nomad LICENSE](https://raw.githubusercontent.com/hashicorp/nomad/main/LICENSE) — Licensor, Additional Use Grant, Change Date
- [GStreamer hardware-accelerated decoding](https://gstreamer.freedesktop.org/documentation/tutorials/playback/hardware-accelerated-video-decoding.html) — `va`/`nvcodec`/`v4l2` plugins, rank-based selection
- [KVS producer SDK on Raspberry Pi](https://github.com/awslabs/amazon-kinesis-video-streams-producer-sdk-cpp/blob/master/docs/raspberry-pi.md) · [aarch64 cross-compile issue](https://github.com/awslabs/amazon-kinesis-video-streams-producer-sdk-cpp/issues/827)

*Checked against current documentation, 2 September 2026.*
