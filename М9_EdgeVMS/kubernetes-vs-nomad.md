# Kubernetes vs Nomad for an Edge VMS

**A decision record for М9_EdgeVMS.** Written after the module was first designed around k3s, in answer to "why not Nomad?" — which turned out to be a better question than the original design deserved.

---

## Verdict

Nomad is the better *technical* fit for a VMS appliance. The original k3s choice rested mainly on non-technical grounds — employability and the fact that Fleet, chosen earlier for multi-site management, is Kubernetes-only.

Two things push back the other way, and both are real: **licensing** (Nomad is BUSL, owned by IBM) and **GitOps maturity** (Nomad has no pull-based reconciler comparable to Fleet).

---

## Where Nomad genuinely wins

### Task drivers beyond containers

The decisive one for a VMS. Nomad's driver architecture is pluggable, with documented drivers including **Podman**, **exec2** and **virt** (beta), alongside the long-standing docker/exec/java/qemu set. Kubernetes schedules containers, full stop.

For a video product this matters concretely:

- A native GStreamer process needing direct device access doesn't have to be containerised
- Legacy binaries schedule alongside modern containers
- Hardware access (codecs, GPUs, capture cards) is far less fiddly outside a container sandbox

### The Podman driver preserves earlier learning

This one is specific to the course, and it is a large gain. Part A teaches Podman and Quadlet. Under Kubernetes, that knowledge is discarded — Quadlet units get rewritten as Deployments, and the module has to admit it's a rewrite rather than a migration. Under Nomad, **Podman remains the runtime** and Nomad becomes a scheduler above it. The module stops throwing away a lesson.

### Operational surface

No CRDs, no operators, no CNI menagerie, no admission controllers. Servers and clients, and a raft consensus group per region. Materially less to run and less to teach.

### Multi-region federation is native

Nomad federates regions over a gossip protocol. Regions are fully independent — they don't share jobs, clients or state, and nothing replicates between them — but you can "submit jobs to any region or query the state of any region transparently," with requests forwarded to the right regional servers.

For a fleet of camera sites that is arguably a cleaner primitive than N independent clusters plus a reconciler stitching them together.

---

## Where Kubernetes wins

| | k3s | Nomad |
|---|---|---|
| Licence | Apache 2.0, CNCF | **BUSL**, Licensor: IBM |
| Ecosystem | Everything ships a Helm chart | HCL jobspec, thinner |
| GitOps | Fleet / Argo / Flux, pull-based | Nomad Pack + CI, push-based |
| Hiring pool | Very large | Small |
| Non-container workloads | No | **Yes** |
| Community forks under pressure | n/a | none emerged |

### The GitOps gap is the substantive one

Fleet is **pull-based**: an agent at each site pulls desired state from Git and reconciles locally. A site that drops offline for a day catches up by itself when it returns, and drift self-heals.

Nomad's story is **Nomad Pack** — Helm-like templating and packaging, with remote registries — driven from CI. That is **push-based**: your pipeline must reach each region to deploy.

For edge sites on flaky links, pull-based reconciliation is materially better, and Nomad's documentation offers no first-class equivalent. Federation softens this (submit once, let Nomad forward) but does not eliminate it: something still has to reach a server.

This is the clearest thing given up by switching.

---

## Licensing — read the licence, not the FAQ

Taken from the licence text in the Nomad repository rather than marketing material:

| Field | Value |
|---|---|
| **Licensor** | International Business Machines Corporation |
| **Licensed Work** | Nomad 1.7.0 or later, © 2024 IBM Corp |
| **Additional Use Grant** | Production use permitted provided the work is not offered to third parties on a hosted or **embedded** basis to compete with IBM's paid versions |
| **Competitive offering** | A product offered to third parties on a paid basis that "significantly overlaps with the capabilities of IBM Corp's paid version(s)" |
| **Change Date** | Four years from each version's initial publication |
| **Change License** | MPL 2.0 |

**Reading it plainly:** a VMS does not significantly overlap with Nomad Enterprise, so embedding Nomad inside a video appliance appears permitted by the Additional Use Grant.

**Why it still deserves counsel:** the grant uses the word *embedded* explicitly, which is exactly what a shipped appliance does; and the Change Date is per-version and rolling, so staying on a supported release means running BUSL code permanently, never reaching the MPL conversion. For any company facing investor or acquirer diligence, a BUSL component shipped inside the product is a standard question.

*This is a summary of licence text, not legal advice.*

**One further signal:** Terraform's licence change produced OpenTofu and Vault's produced OpenBao. No comparable fork emerged for Nomad. That says something about relative community gravity — and community size feeds directly back into the hiring-pool and ecosystem columns above.

---

## What this means for the course

| Criterion | Favours |
|---|---|
| Technical fit for a VMS appliance | **Nomad** |
| Continuity with Part A (Podman) | **Nomad** |
| Simplicity to teach | **Nomad** |
| Student employability | **Kubernetes** |
| Multi-site GitOps maturity | **Kubernetes** (Fleet) |
| Licence cleanliness for a product | **Kubernetes** |

The module has been rewritten around Nomad. The two costs above are carried explicitly rather than hidden: the GitOps chapter confronts push-versus-pull head-on, and the licensing question is taught as part of the module rather than left as a footnote — students shipping commercial products need to know how to read a BUSL grant.

Neither tool wins the single-server case. Podman with Quadlet beats both there, and Part A says so.

---

## Sources

- [Nomad architecture](https://developer.hashicorp.com/nomad/docs/architecture) — servers/clients, raft, regions, gossip federation
- [Nomad task drivers](https://developer.hashicorp.com/nomad/plugins/drivers) — pluggable drivers, Podman, exec2, virt
- [Nomad Pack](https://developer.hashicorp.com/nomad/tools/nomad-pack) — templating, registries, Helm comparison
- [Nomad LICENSE](https://raw.githubusercontent.com/hashicorp/nomad/main/LICENSE) — Licensor, Additional Use Grant, Change Date
- [Nomad CE licence & support](https://developer.hashicorp.com/nomad/docs/ce-license-support)
- [HashiCorp BSL announcement](https://www.hashicorp.com/en/blog/hashicorp-adopts-business-source-license)
- [k3s requirements](https://docs.k3s.io/installation/requirements)
- [Fleet core concepts](https://fleet.rancher.io/explanations/concepts)

*Checked against current documentation, 2 September 2026.*
