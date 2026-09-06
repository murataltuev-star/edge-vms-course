# Glossary

Terms the course uses precisely, and acronyms it would otherwise leave unexplained. Where a term has an everyday meaning and a narrower one here, the narrower one is what the lessons mean.

## The three words the course keeps apart

| Term | Meaning here |
|---|---|
| **Node** | A VMS instance — its own database, its own cameras, its own archive index. М10 builds one. From М11 it is a scheduler allocation with stable identity that **moves between servers**, carrying its cameras with it. **A Node is not a server.** |
| **Server** | A box with CPUs and disks, running whichever Nodes the scheduler places on it. A Nomad *client*. |
| **Site** | Where cameras physically are — a building, a store. The only one of the three an operator names. |

Two more that are easy to blur:

| Term | Meaning here |
|---|---|
| **Domain** | The largest set of servers sharing a network you would bet recording on. One domain, one directory. Past that boundary you federate rather than build a bigger domain. |
| **Federation** | Independent domains cooperating under a shared trust root. **Not** Nomad's "federation", which joins scheduler regions — a different plane, and М12 says so on its first page. |

---

## State and correctness

| | |
|---|---|
| **Desired state** | What an operator asked for. Persisted. |
| **Actual state** | What is running right now. Observed, never persisted — anything a system remembers about actual state across a restart is a bug. |
| **Reconciliation** | The loop that closes the gap between the two. A Nomad jobspec plus its scheduler is one; М10's AppHost is another. |
| **Revision** | A monotonic, controller-assigned integer per object. *Applied* means `observed_revision >= revision`. |
| **Epoch** | A fencing token: increments on every ownership change, never decreases, issued by exactly one authority. Part of the archive path, so a stale writer cannot name the files it would otherwise corrupt. |
| **Fencing** | Making a stale writer's writes harmless rather than trying to stop them. You cannot stop a zombie from writing. |
| **CAS** — check-and-set | An update that succeeds only if the value has not changed since you read it. Nomad Variables compare against `ModifyIndex` and return 409 on conflict; it is how the course issues epochs. A **lock** without a fencing token is not a substitute. |
| **Split-brain** | Two writers both believing they own the same object. For a key-value store, survivable. For video, corruption — footage has no merge function. |
| **Quorum** | The majority a consensus group needs to agree. Why an odd number of servers, and why a witness node exists. |

---

## Availability and recovery

| | |
|---|---|
| **HA** — high availability | Keeping a service running through the failure of one of its parts, normally with a standby ready to take over. In М11 the question is whether the *directory* needs one. |
| **Failover** | Moving work off something that failed. In this course it happens at two levels: the scheduler moves a **Node** to a healthy server; the domain reassigns **cameras** only when an operator rebalances. |
| **RPO** — recovery point objective | How much recently written data an outage may lose, measured in time. Here it is the interval between a Node changing its configuration and publishing it upward. A number the product states, not a surprise it discovers. |
| **RTO** — recovery time objective | How long recovery is allowed to take. In М11's deliverable it is the seconds between pulling the power and recording resuming. |
| **TTL** — time to live | How long a lease is valid before it expires and the object may be granted to someone else. |
| **Witness / monitor node** | A member of a database cluster holding no data, present only to break ties and prevent split-brain. |

---

## Storage and process

| | |
|---|---|
| **RSS** — resident set size | Memory a process has in RAM. Counts a shared library page once **per process**, so summing it across processes double-counts and inflates the answer. |
| **PSS** — proportional set size | The same, but each shared page is divided by the number of processes mapping it. **The only honest way** to compare one process holding N pipelines against N processes holding one each. |
| **GIL** — global interpreter lock | CPython's lock ensuring one thread runs Python bytecode at a time. Released around C calls, which is why fifty GStreamer pipelines in one Python process work — and re-acquired in every callback, which is why a per-buffer callback does not. |
| **CSI** — Container Storage Interface | The standard by which schedulers attach network storage to workloads. Nomad supports it; М11 Lesson 27 explains why it does not give an appliance unattended failover. |
| **SAN / NAS** | Storage area network / network-attached storage — shared storage reachable by several servers. Expensive, and a shared failure domain. |

---

## Security and identity

| | |
|---|---|
| **Authentication** | Proving *who* a subject is. Federated in М12, so there is one Alice across every Node and domain. |
| **Authorization** | Deciding what that subject may *do*. Deliberately **not** centralised: grants live in each Node so they can be checked with the domain unreachable, and carry an expiry that bounds the revocation window. |
| **PKI** — public key infrastructure | The certificate authorities, certificates and revocation machinery that let parties prove who they are. |
| **CA** — certificate authority | What signs certificates. М12 uses an offline root and one intermediate per domain, so routine issuance never leaves the site. |
| **mTLS** — mutual TLS | Both ends of a connection present certificates, so the server authenticates the client as well as the reverse. |
| **CRL / OCSP** | Certificate revocation list / Online Certificate Status Protocol — the two ways to ask whether a certificate has been revoked. Both assume you can reach something, which is why М12 prefers short lifetimes at the edge. |
| **TPM** — Trusted Platform Module | A chip holding a key that cannot be exported, and able to attest what software booted. The strongest answer to *how does a box with no secret get one*. |
| **BRSKI** | Bootstrapping Remote Secure Key Infrastructure ([RFC 8995](https://datatracker.ietf.org/doc/html/rfc8995)) — the standard for secure zero-touch onboarding. |
| **IDevID / LDevID** | Initial / Local Device Identifier — the certificate a manufacturer installs at the factory, and the one a domain issues once the device has joined. |
| **MASA** | Manufacturer Authorized Signing Authority — the vendor-run service that vouches for a device to a domain. Running one is a permanent commitment, which М12 states plainly. |
| **EST** | Enrollment over Secure Transport — how a device asks a CA for its certificate once it has decided whom to trust. |

---

## Licensing

| | |
|---|---|
| **BUSL** — Business Source License | A source-available licence: the source is published, production use is granted subject to conditions, and each version converts to an open-source licence after a **Change Date**. Nomad and Consul are BUSL with IBM as Licensor. |
| **Change Date** | When a given version's BUSL grant converts — four years per version for Nomad, to MPL 2.0. Staying on supported releases means never reaching it. |
| **Additional Use Grant** | The clause defining what production use is permitted. Nomad's uses the word *embedded*, which is what a shipped appliance does — hence the discussion in [`kubernetes-vs-nomad.md`](./М11_DomainVMS/kubernetes-vs-nomad.md). |
| **MPL 2.0** | Mozilla Public License 2.0 — a weak-copyleft open-source licence; what BUSL components convert to. |

*Summaries of licence terms, not legal advice.*
