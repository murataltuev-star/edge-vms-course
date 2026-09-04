# RAUC and Its Alternatives

**A decision record for М9_EdgeVMS.** Companion to [`kubernetes-vs-nomad.md`](kubernetes-vs-nomad.md), written in answer to "are there alternatives to RAUC?" — the same kind of question, and it produced the same kind of answer: the incumbent choice survives for the course, but probably shouldn't for the product.

---

## Verdict

RAUC stays in the course. For the appliance a commercial vendor would actually ship, **bootc deserves a serious evaluation** — because the target is x86-64 UEFI and the stack is already container-native, which inverts RAUC's usual advantage.

Separately, **hawkBit** is now more important than it looked, because moving from Fleet to Nomad cost us pull-based reconciliation and hawkBit gives some of it back on the OS plane.

---

## The field

| Tool | Licence | Update model | Server included | Notes |
|---|---|---|---|---|
| **RAUC** | LGPL-2.1 | A/B partitions, recovery, multi-slot | No | Broadest bootloader support — barebox, U-Boot, GRUB, EFI. Signature verification is unconditional. Supports encrypted updates. Ships on Steam Deck. |
| **SWUpdate** | GPL-2.0 | Handler pipeline; A/B or recovery | Via Eclipse hawkBit | The most flexible: custom C/Lua handlers, streaming from stdin. Active since 2013. |
| **Mender** | Apache-2.0, features gated | A/B only | **Yes** | A complete platform. Tight server coupling makes migrating away difficult; delta updates are commercial-only. |
| **ostree / bootc** | LGPL-2.0+ | Content-addressed object store, hardlink deployments | No | No A/B storage overhead. Large desktop/server ecosystem. Embedded integration is "substantial" work. |
| **Rugix Ctrl** | MIT/Apache-2.0 | A/B, recovery | No | Rust, memory-safe. 1.0 in February 2026. Native delta via content-defined chunking. |
| **systemd-sysupdate** | LGPL-2.1 | Versioned resources | No | Ships *inside* systemd — no extra dependency at all. |

**On systemd-sysupdate:** I confirmed directly that the binary is present in systemd 255, exposing `list`, `check-new`, `update`, `vacuum`, `pending`, `reboot` and `components`. What I have *not* assessed is its maturity for appliance use — the comparison literature doesn't cover it, and "ships with systemd" is an argument about dependencies, not about production readiness. Worth a look precisely because this design is already systemd-heavy, but look before committing.

---

## bootc: the one that may beat RAUC here

bootc performs transactional, in-place OS updates using **OCI/Docker container images**. The image carries a Linux kernel and acts as the transport; the base userspace is *not* containerised — systemd still runs as pid1 in the ordinary way. Underneath it uses ostree, which has been doing transactional OS updates for years. The CLI and API are now declared stable.

Why this matters specifically for this appliance:

The design already runs everything through Podman, from a container registry, with container image signing. bootc means **the operating system travels through the same registry, the same pull mechanics, and the same signing scheme as the application images.** Two delivery pipelines with two artifact formats and two trust chains collapse into one.

And consider where RAUC's advantages actually live: barebox and U-Boot support, embedded storage handling, NAND/NOR awareness. Those matter most on ARM SBCs — which is precisely the platform this module is *not* targeting. On an x86-64 UEFI mini-PC, bootc is on home turf and RAUC is spending strength that goes unused.

**The counter-argument**, and it's real: A/B partitions have blunt, obvious failure semantics that are easy to teach and easy to reason about at 3am — one slot is running, the other is being written, the bootloader picks. ostree's model is more space-efficient and less immediately legible. For a first appliance from a team new to atomic updates, legibility has value.

---

## hawkBit: partially fixing what the Nomad switch broke

Dropping k3s for Nomad cost Fleet's pull-based reconciliation, where an agent at each site pulls desired state and heals drift locally. Nomad Pack plus CI is push-based, which is worse for sites on flaky links.

**Eclipse hawkBit** returns some of that on the OS plane. SWUpdate integrates with it directly; RAUC has `rauc-hawkbit-updater`. Being Eclipse-governed, it carries no vendor lock-in — unlike Mender, which bundles a server but makes leaving hard.

This makes the design doc's line about hawkBit being "the production answer, not taught" more load-bearing than when it was written. It may deserve promotion into the module rather than a footnote.

---

## Licensing

After the Nomad BUSL discussion, worth stating plainly: **there is no BUSL anywhere in this list.**

- **LGPL** — RAUC, ostree/bootc, systemd-sysupdate. Friendly to linking and shipping in a commercial appliance.
- **MIT/Apache** — Rugix, Mender client. Most permissive.
- **GPL-2.0** — SWUpdate. If you modify it and ship it inside an appliance, that is a copyleft obligation to understand before committing. Using it unmodified is a different question from patching it.

*Summary of licence terms, not legal advice.*

---

## What this means

| Criterion | Favours |
|---|---|
| Teaching the concepts clearly | **RAUC** — A/B is legible, signing is unconditional |
| Lessons that port to ARM later | **RAUC** — widest bootloader coverage |
| Fit with a container-native x86-64 appliance | **bootc** — one registry, one signing chain |
| Pull-based fleet updates | **hawkBit** with either RAUC or SWUpdate |
| Fewest dependencies | **systemd-sysupdate** — already installed |
| Complete platform, fastest to something working | **Mender** — at the cost of lock-in |

**Course decision: keep RAUC.** Widest bootloader coverage means Lessons 16–19 port to ARM with only the bootloader chapter changing. Unconditional signature verification makes Lesson 17's security content real rather than optional. And it is the best-documented of the set, which matters when students are learning the concept rather than the tool.

**Product recommendation: evaluate bootc.** The target platform and the container-native stack both point at it, and collapsing two delivery pipelines into one is a genuine reduction in moving parts for a team shipping appliances.

These are not in conflict. The course teaches atomic updates, rollback, signing and the OS/app/data boundary — all of which transfer to bootc unchanged. Only the tool differs.

---

## Sources

- [Comparing open-source OTA update engines (February 2026)](https://rugix.org/blog/2026-02-28-ota-update-engines-compared/) — licences, models, maturity across RAUC, SWUpdate, Mender, ostree, Rugix
- [bootc documentation](https://bootc.dev/bootc/) — OCI images as OS transport, ostree relationship, API stability
- [RAUC integration](https://rauc.readthedocs.io/en/latest/integration.html) — bootloader backends and their tooling
- `systemd-sysupdate --help`, systemd 255 — verified directly

*Checked against current documentation, 4 September 2026.*
