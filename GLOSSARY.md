# Glossary

Terms the course uses precisely, and acronyms it would otherwise leave unexplained. Where a term has an everyday meaning and a narrower one here, the narrower one is what the lessons mean.

## The three words the course keeps apart

| Term | Meaning here |
|---|---|
| **Node** | A VMS instance — its own database, its own cameras, its own archive index. М10 builds one. From М11 it is a scheduler allocation with stable identity that **moves between servers**, carrying its cameras with it. **A Node is not a server.** |
| **Server** | A box with CPUs and disks, running whichever Nodes the scheduler places on it. A Nomad *client*. |
| **Domain signer** | The one Nomad job at the domain that holds keys and signs — certificates for machines, tokens for people. The only domain service that cannot be re-provisioned from nothing, and the reason М13's delegated root matters. Hosted by one designated cluster. |
| ~~**Domain controller**~~ | **Retired.** Named a component the design dissolved: its database became cluster Variables and object stores, its scheduler became Nomad, its API moved onto every Node, its restore point moved into the cluster. What remains at the domain is a signer, a stateless placement function and a read view — none of them authoritative, which is why the old name misleads. |
| **Site** | Where cameras physically are — a building, a store. The only one of these an operator names. **Sites and clusters are many-to-many on purpose:** a campus is one domain with three clusters and three sites; a cloud deployment is one domain with one cluster serving fifty sites. |

Two more that are easy to blur:

| Term | Meaning here |
|---|---|
| **Cluster** | Servers close enough to share **a network you would bet recording on** — one LAN, usually one server room. What М11 builds, and the boundary set by **physics**. A Node fails over *within* a cluster and **never across one**, because its footage is on that cluster's disks. |
| **Domain** | The **clusters** under one directory, one CA and one set of operators. What М12 builds, and the boundary set by **administration** rather than physics — one customer installation, which may be one cluster or several. A cluster answers *where can this run*; a domain answers *what is supposed to be running, and in which cluster*. **Losing a cluster stops that cluster's cameras; losing the domain stops nothing already recording.** Past the domain you federate rather than build a bigger one. |
| **Federation** | Independent domains cooperating under a shared trust root. **Not** Nomad's "federation", which joins scheduler regions — a different plane, and М12 says so on its first page. |
| **Orchestration** | Two scopes, kept apart. Nomad *orchestrates* within a domain: it places allocations on servers. М12 **OrchestratedVMS** orchestrates above domains: it decides which domain and whose hardware there is to place on at all. |
| **Edge / cloud / mixed** | Where a domain's servers physically are — at the site, rented in a cloud region, or both in one system. A placement decision per site, not three products: a Node cannot tell which it is running on. |

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
| **RPO** — recovery point objective | How much recently written data an outage may lose, measured in time. Every boundary in this course is crossed the same way — **a one-way publication with a stated RPO** — whether what crosses is footage (М9's spool), configuration (М11) or status. A number the product states, not a surprise it discovers. |
| **RTO** — recovery time objective | How long recovery is allowed to take. In М11's deliverable it is the seconds between pulling the power and recording resuming. |
| **TTL** — time to live | How long a lease is valid before it expires and the object may be granted to someone else. |
| **Witness / monitor node** | A member of a database cluster holding no data, present only to break ties and prevent split-brain. |

---

## Storage and process

| | |
|---|---|
| **Spool** | Segments recorded locally and waiting to be uploaded, deleted only on acknowledgement. Introduced in М9 so an uplink outage costs visibility rather than footage; М10 puts an index over the same files and they become the archive. |
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
| **CA** — certificate authority | What signs certificates. Each domain runs one (М12); М13 replaces its self-signed root with an intermediate delegated from an offline root, so routine issuance never leaves the site. |
| **Trust anchor** | The certificate a party has decided to trust without further proof — the top of any chain it will accept. Delegating one downward is possible; delegating a secret is not, which is why domains run a CA and not a vault. |
| **Cross-signing** | Issuing a second certificate for the same key from a different CA, so a chain validates under both the old and the new anchor at once. How a trust anchor is replaced without a flag day. |
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
| **Change Date** | When a given version's BUSL grant converts — four years after that version is published, to MPL 2.0 (Nomad 1.8.0 → 28 May 2028). Not an escape route: a version reaches its Change Date about two years *after* its support ends, so the MPL build is always the unpatched one. |
| **Additional Use Grant** | The clause defining what production use is permitted. Nomad's forbids offering the software to third parties *hosted or embedded* **in competition with the licensor's paid products** — and "embedded" is itself defined relative to *a competitive product*, so embedding alone is not the trigger. A VMS does not overlap Nomad Enterprise, so shipping an appliance with Nomad inside is permitted; see [`COURSE-PLAN.md`](./COURSE-PLAN.md). |
| **MPL 2.0** | Mozilla Public License 2.0 — a weak-copyleft open-source licence; what BUSL components convert to. |

*Summaries of licence terms, not legal advice.*
