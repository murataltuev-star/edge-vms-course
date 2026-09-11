# Module 9 — EdgeVMS: Shipping the VMS as an Appliance

The cloud VMS from [Module 8](../М8_KVS_VMS/README.md) runs on a computer somebody administers. This module turns it into one that nobody does — a box in a ceiling void that updates itself over a network, recovers from its own bad updates, and keeps recording when the link goes down.

Four lessons, one box. The full design brief is in [`module-design.md`](module-design.md).

## The thesis

There are **two independent update planes** in any real edge product, and conflating them is how you end up needing an OS flash to change a setting:

| Plane | Question it answers | Tool here | Changes when |
|---|---|---|---|
| **Below** | What operating system is this box running? | RAUC | You ship a new appliance image |
| **Above** | What workload is running on it? | Podman and Quadlet | You ship a new app version |

And a third thing that belongs to neither: **the data**. It must outlive both planes, which sounds obvious and is the source of this module's two most expensive mistakes — container storage left in a rootfs slot, and footage that only existed in flight.

## Lessons

| # | Lesson | You'll be able to... |
|---|---|---|
| 1 | [The Appliance Problem, and Your Test Bench](01-the-appliance-problem-and-your-test-bench.md) | Explain why in-place updates are unsafe on an unattended box; describe A/B slots and what is *not* duplicated; size a data partition from a stated outage requirement; build and boot a QEMU UEFI bench with two independent root filesystems. |
| 2 | [RAUC: Slots, Bundles, and Signatures](02-rauc-slots-bundles-and-signatures.md) | Write a `system.conf` with current keys; build a two-level CA; sign a bundle; **demonstrate** that a wrongly-signed, wrong-hardware, or tampered bundle is refused; install into the inactive slot with the running one untouched. |
| 3 | [Rollback That Actually Works](03-rollback-that-actually-works.md) | Explain why "did it boot?" is not success; implement the try/OK state machine in GRUB; write a health check that reaches *is footage being written*; induce three failures and document each recovery. |
| 4 | [Podman, Quadlet, the Three-Way Boundary, and the Spool](04-podman-quadlet-and-the-spool.md) | Run the VMS as systemd-managed containers; move container storage off the rootfs; provision credentials at commissioning; **watch footage vanish during an outage**, then build the spool that stops it. |

## What each lesson leaves running

Every lesson ends with something you can see working, and each one is the input to the next.

- **16** → one disk, two complete systems, either bootable, chosen by hand at a menu.
- **17** → that menu replaced by a signed artifact, and three refusals proving the signature check is real.
- **18** → the choice made automatically, with a broken update rolling itself back and nobody in the room.
- **19** → the VMS running under systemd across reboots and OS updates, recording through a ten-minute uplink outage with nothing lost.

## What you can verify without hardware

The module is honest about this because a course that pretends otherwise gets found out at the worst moment.

**Runs anywhere:** the whole PKI and signing chain (`openssl` only — the outputs printed in Lesson 2 are real), the spool and its tests (Python only), partition arithmetic, and any shell logic.

**Needs the QEMU bench:** slot selection, RAUC install, GRUB boot logic, rollback, Quadlet.

**Needs real hardware, and no bench can substitute:** a driver that works in QEMU and not on the board you ship. Lesson 3 Exercise 5 is about what that means for your release process.

One correction worth knowing before you try it: **`systemd-analyze verify` cannot check a Quadlet `.container` file** — it does not know the `[Container]` section and ignores the whole thing. Use `podman-system-generator --dryrun` instead (Lesson 4, Step 3).

## Two conventions carried from Module 8

**Every step produces a result you can see.** A partition table you print, a signature that fails in front of you, a boot menu showing its own state variables, ten minutes of footage that is either there or is not.

**Each lesson replaces a stand-in from the one before.** The hand-selected boot menu becomes a signed bundle; the signed bundle becomes a self-healing rollback; the hand-started container becomes a systemd unit. What this module *adds* to that discipline is naming its stand-ins as debts to be collected later — the AWS credentials in Lesson 4 Step 4 are the first of five temporary secrets, and М12 collects them all — four replaced by giving things identities, and one promoted to the customer's permanent root.

## Decision records

Written alongside the module, with their costs attached rather than quietly omitted:

- [RAUC alternatives](rauc-alternatives.md) — SWUpdate, Mender, bootc, systemd-sysupdate, and where each wins. RAUC is taught because A/B slots are legible and its signature verification is unconditional; **bootc may well be the better choice for a product shipping on x86-64 UEFI**, and the document says so.
- [One container per camera?](apphost-and-process-model.md) — the process model at 1000 cameras, and why the orchestrator must not own camera lifecycle. Lesson 4's second sidebar sets this up; М11 Lesson 1 breaks the pattern deliberately.

The multi-node half of this module moved to [М11](../М11_ClusterVMS/module-design.md), where Nodes are scheduled across servers. A module called EdgeVMS should not build a raft cluster. The orchestrator comparison that shaped it is recorded in [Kubernetes vs Nomad](../М11_ClusterVMS/kubernetes-vs-nomad.md).

## The artifacts, whole

[`edgevms/`](./edgevms/README.md) assembles what the four lessons leave on the box: `bench/build-disk.sh` builds the A/B disk with everything that must be *in the image* installed before slot A is copied to B; `pki/` is the two-level CA and the three `openssl cms` proofs; `rauc/` is `system.conf`, the manifest and a bundle builder with `--broken-kernel`, `--broken-config`, `--rogue` and `--wrong-hardware` for Lessons 2 and 3; `boot/grub.cfg` is the ORDER/OK/TRY state machine; `quadlet/` and `spool/` are Lesson 4. One thing in it is newer than the lessons: **the health check's third row is no longer a stand-in.** When М10's Node is installed it reads `nodevms_camera_silent_seconds_max` from the Node's own `/metrics`, locally, with the uplink down — so Lesson 3's rollback decision is finally made on *is footage being written*, which is what the lesson said it had to be.

```bash
cd edgevms && pki/make-ca.sh && pki/verify-chain.sh && python3 spool/test_spool.py   # no VM needed
```

## Where this goes

**М9 has no desired state.** You flash an image and containers run; actual state is the only state there is, and this module's entire job is making that replaceable safely.

[**М10 — NodeVMS**](../М10_NodeVMS/module-design.md) introduces the wish: a row saying a camera *should* be recording, and a loop that closes the gap. Its first act is to put an index over the segments Lesson 4 started writing — turning a spool into an archive.
