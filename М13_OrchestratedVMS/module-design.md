# М13_OrchestratedVMS — Module Design

**The layer that supplies what a domain cannot make for itself — and the first one allowed to be unavailable.**

Edge, Node and Domain each widened the scope of truth and added a new class of disagreement. This module adds the last one: things that must be true **above** any single domain — who a box is, who a person is, what a customer is entitled to, and what the fleet actually consists of.

It also supplies something no earlier layer could: **capacity.** This layer runs in a cloud — a public one, or the customer's own private one — and multiple domains operate through it. Because it can hand out allocations, a Node no longer has to run on hardware standing in the building the cameras are in.

> **The consequence, and the thing the module is really about: a system can be built at the edge, in the cloud, or mixed — and it is the same system either way.** Edge and cloud stop being two products with two codebases and become a placement decision, taken per site, on numbers a student can compute.

That closes the arc. **М8 rented a cloud VMS**: Kinesis held the configuration and the archive, and the student's software was a client of somebody else's service. Eleven modules later the same product exists with nothing rented — your Nodes, your archive, your allocations — and М9's hand-provisioned AWS credentials, the first stand-in the course ever left, are finally retired by not needing them.

It is also the first layer that may be switched off without the product stopping, and that constraint shapes every decision in it.

> **Scope note.** [`COURSE-PLAN.md`](../COURSE-PLAN.md) originally had these as two modules three apart: М12 for secrets and PKI, М14 for device management. They asked the same question twice — *"how does a machine prove who it is to get its first secret?"* and *"how does a box join and get an identity without someone typing secrets into it?"* — and neither owned it. They are merged here. The seven-layer model had identity as layer 5 and device management as layer 7; building it revealed they are one layer, and enrollment is where they meet.

> **Two words that each mean two things, and the course keeps them apart.**
>
> **Orchestration.** М11 calls Nomad *the orchestrator* — it places allocations on servers inside one domain. This module is *OrchestratedVMS* because it decides something one level up: **which domain, and whose hardware, a workload lands on at all.** Nomad still does the placing; this layer decides what there is to place on. Same verb, two scopes — exactly like Node and Server, and the module says so on its first page rather than letting students merge them.
>
> **Federation.** Lesson 36 teaches Nomad *federation* — regions joined by gossip, sharing no state. That is the **workload** plane spanning sites. The product plane — trust, identity and entitlement spanning domains — is a different federation, and the module teaches them adjacently on purpose, because students who meet the two a module apart tend to merge them.

---

## The thesis

> **Everything below this layer must keep working when this layer is unreachable.**

A site records video whether or not the centre answers. The node converges. The domain places cameras. This layer supplies identity, trust, entitlement and capacity — and every consumer of the first three must **cache them and degrade on a grace period**, never block on them.

This is what makes the arrangement a federation rather than a hierarchy, and it is not a nicety. A VMS whose cameras stop recording because a certificate service is down has failed at the only job it has.

### The objection this raises, and its answer

If this layer hands out the allocations a domain runs on, how can it also be allowed to be down?

**Because it supplies capacity once, and authority continuously — and only the second is a runtime dependency.** A cloud-hosted domain has its own Nomad servers, its own directory, its own CA, and its own Nodes owning their own configuration; it is a domain in every sense М11 defined, and the only difference is who owns the hardware invoice. Losing this layer means you cannot *provision* a new domain or move one. It does not mean an existing domain stops.

The definition М11 arrived at already covers the cloud case without amendment:

> **A domain is the largest set of servers sharing a network you would bet recording on.**

Rented servers in one cloud region share such a network. So they can be a domain. Servers in a building share such a network. So they can be a domain. **A site's cameras and a cloud Node do not** — the uplink is exactly the link the whole course says you must not bet recording on — which is why the interesting configuration is not "cloud" but *mixed*, and why the next section is arithmetic rather than opinion.

The rule produces three consequences that this module spends nine lessons on:

| Because the centre may be unreachable… | …the design must |
|---|---|
| A box cannot ask permission to boot | carry an identity it can prove offline |
| A service cannot fetch a certificate on demand | renew from something inside its own domain |
| An appliance cannot consult a vault to start | not need a vault to start |

---

## The demo it is built backwards from

A box arrives at a site in a cardboard carton. Nobody types a secret into it. It is powered on and given a network, and within minutes it has proved who it is, received a certificate, fetched its configuration, joined its domain and started recording.

**A second site has no box at all.** Its six cameras stream to Nodes running on rented instances, provisioned from the same console, and an operator watching both sites cannot tell from the console which is which — because there is nothing to tell.

Then the first site's uplink is cut for **thirty days**. It keeps recording. Service certificates renew from inside the domain. Entitlement holds on its grace period. When the link returns, inventory catches up, version skew is reported, and nothing was lost. The cloud site, meanwhile, is down for the duration — **and the module says why that is the correct outcome rather than a defect**, because a site that chose not to buy hardware chose its uplink as its failure mode.

Finally the box is marked stolen, and loses access on a schedule the student can state in advance.

---

## Edge, cloud, or mixed — and the number that decides

The module's second claim is that these are one product. The student proves it by deploying the same Node three ways and finding the software identical. What is *not* identical is what crosses the uplink, and that is a calculation, not a preference.

| | Where Nodes run | What crosses the uplink | Fails when |
|---|---|---|---|
| **Edge** | on hardware at the site | status, config publications, alarms — kilobytes | never, for recording. The site is autonomous |
| **Cloud** | on rented servers in a region | **every camera's full bitrate, continuously** | the uplink hiccups. There is no local copy |
| **Mixed** | at the site, with the domain's operation in the cloud | the same kilobytes, plus whatever the operator is watching right now | nothing that matters. **This is the default** |

**The arithmetic that rules out pure cloud for most sites**, and students should compute it before reading the answer:

```
50 cameras × 4 Mbps  =  200 Mbps sustained upstream, 24/7
                     =  ~2 TB per day leaving the building
```

Sites with that upstream exist. Most retail stores, schools and small industrial sites do not have it, cannot buy it, and would not like the bill if they could. So:

- **Cloud Nodes are for small sites** — a handful of cameras, no hardware to install, no one on site to install it. A real product need, and the reason this is not an edge-only course
- **Edge Nodes are for everything else**, and М9–М11 already built them
- **Mixed is the shape a real deployment takes**: recording stays where the cameras are, and everything an operator *does* — the console, placement, identity, entitlement — comes from the cloud

**What must be true for this to be one product rather than two:** a Node cannot know where it is running. It reads its configuration, records, publishes upward, and renews its certificate identically on a rack in a warehouse and on a rented instance. That property was not added for this module — it is what М10's reconciler and М11's Node-owned configuration have been buying all along, and this is where the course collects on it.

**The honest residue:** a cloud Node still needs the camera's stream to reach it, and a camera behind a customer's NAT with no local Node is a connectivity problem this module does not solve. Either something at the site pushes (which is an appliance, and now you are mixed), or the camera itself does (which is a camera capability, not a design choice you get to make).

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Availability | **The orchestration layer may be down** | Stated first because everything else follows from it. |
| Deployment model | **Edge, cloud or mixed — one codebase, a placement decision per site** | A Node that cannot tell where it is running is the payoff for М10's reconciler and М11's Node-owned configuration. |
| Default shape | **Mixed: record at the edge, operate from the cloud** | Bandwidth decides, and for most sites it decides against streaming every camera upstream. |
| Where this layer runs | **A cloud — public, or the customer's private one** | Multi-tenancy is a business decision; the design must work either way, which is what keeps it deployable on-premises for customers who require it. |
| Device identity | **Hardware-rooted where possible, approved-registration otherwise** | The A/B image is byte-identical on every appliance, so nothing device-specific can live in it. |
| Bootstrap model | **BRSKI as the reference, TOFU-with-approval as the shipped fallback** | The standard exists and is worth teaching. Whether the vendor runs the service it requires is a business decision, not a technical one. |
| CA topology | **Offline root, one intermediate per domain** | The structural move that makes offline tolerance possible: routine issuance never leaves the site. |
| PKI, split by scope | **Issuance is domain-level; the root and the delegation are federation-level** | A CA can be delegated, a vault cannot. М11 builds and runs the domain CA; this module supplies its authority. |
| Revocation | **Short lifetimes, not revocation lists** | CRL and OCSP both assume you can reach something. At the edge you frequently cannot. |
| Vault placement | **Central. The appliance does not run one** | Resolves the unsealing problem by dissolving it — see below. |
| Vault scope | **Dynamic credentials and multi-tenant secrets only** | Most of the course's stand-in secrets are removed by *giving machines identities*, not by storing the secrets better. Lesson 41 audits this rather than assuming it. |
| Inventory | **Reported, never commanded** | М10's rule at fleet scope: desired state is persisted, actual state is observed. |
| Version skew | **A normal operating state, not a fault** | You cannot update a fleet atomically, so the contract must tolerate mixed versions by design. |
| Service mesh | **No Consul** | See [`consul-and-openbao.md`](consul-and-openbao.md). |

---

## Prerequisites

- **М9 Lesson 19** — credentials are provisioned at commissioning, never baked into an image that ships identically to every device. This module finally answers *how*.
- **М9 Lesson 17** — the RAUC signing chain, built with real `openssl`. The PKI lessons here are the same skill, one scope up.
- **М10 Lesson 20** — the hand-provisioned database password, marked temporary. Cashed in at Lesson 41.
- **М12 Lesson 32** — the deliberately unauthenticated API. Also cashed in at Lesson 41.
- **М12 Lesson 33** — mTLS on the Node↔directory streams, from a self-signed domain CA. Lesson 38 replaces the root and leaves everything under it alone.
- **М11 entire** — opaque config and revision ordering are what make version skew survivable, and Lesson 43 collects on that.
- **М11** — Nomad clusters and jobs. Lesson 36 extends that to regions; the rest of the module does not depend on it.

---

## Secure introduction: the hardest problem here

A device with no secret must obtain one, over a network it does not yet trust, from a service it cannot yet authenticate. Every option is a trade, and М9 removed the easiest one: **the appliance image is byte-identical across every unit**, because both A/B slots must be, so nothing device-specific can be inside it.

| Approach | How it fails |
|---|---|
| **Shared secret in the image** | One extracted image is every device's identity. This is how vendors get breached; it is not a trade-off, it is a defect |
| **Per-device token written at manufacture** | Works, but it is a factory process, a secret database, and a secret in transit — you have moved the problem to logistics |
| **Hardware root** (TPM 2.0, or a manufacturer-installed certificate) | Strongest. A key that cannot be exported, and with attestation, evidence of *what software is running* — at the cost of a hardware requirement and a manufacturing step |
| **Registration with human approval** | The device presents itself, an administrator approves it in a console. Pragmatic, widely deployed, and the approval is where judgement lives — but it does not scale to a thousand sites and it trusts the network at first contact |

### BRSKI, because the standard exists

[RFC 8995](https://datatracker.ietf.org/doc/html/rfc8995) specifies secure zero-touch bootstrap without pre-shared keys and without bare trust-on-first-use. Its vocabulary is worth teaching because it names the parts precisely:

- The **pledge** is the new box, carrying a factory-installed **IDevID** — an X.509 certificate that identifies it, authenticates it, and tells it where to find its manufacturer's service
- The **registrar** belongs to the domain and decides whether the pledge may join
- The **MASA**, the manufacturer's signing service, issues a **voucher**: a signed artifact telling the pledge which registrar to trust, carrying that registrar's root as the pinned domain certificate
- Having imprinted on that trust anchor, the pledge enrolls over **EST** and receives an **LDevID** — a locally issued certificate, from *this* domain

Note how cleanly that lands on the course's own vocabulary: BRSKI's *domain* is DomainVMS's domain, the registrar sits in the federated layer, and the LDevID is issued into the domain that will actually run the box.

> **The cost, stated plainly: BRSKI means the vendor runs a MASA.** A service with an availability requirement and a signing key that must never leak, for the lifetime of every appliance ever shipped. That is a real commitment, and a module that teaches BRSKI without saying so is selling something.

**The ladder the module teaches:** never a shared secret in an image; registration-with-approval as the honest starting point; hardware-rooted identity when the appliance has a TPM; BRSKI when customers demand zero-touch across many sites and the vendor will fund the service behind it.

---

## Lifetimes against offline tolerance

The central tension, and it has an arithmetic answer.

- **Short certificates** revoke by expiring — compromise ends quickly — but a site offline longer than the lifetime goes dark.
- **Long certificates** survive outages and keep a stolen device trusted for months.

There is no lifetime that is good at both, so stop looking for one and **split the certificates by job**:

| Certificate | Lifetime | Renewed by | Needs the centre? |
|---|---|---|---|
| Root | Years, offline | Ceremony | — |
| **Domain intermediate** | ~1 year | The centre | **Once a year** |
| Service-to-service, inside a domain | Hours to days | The domain's own intermediate — **built in М11** | **Never** |
| Device identity (LDevID) | Long | The centre | On enrollment, and on renewal |

**Delegating an intermediate CA to each domain is the structural move that makes the thesis true.** Routine issuance and renewal happen inside the site, at whatever frequency good hygiene wants, and the only thing that ever needs the centre is the intermediate's own annual renewal.

The number students should be able to state:

```
maximum tolerable outage  =  certificate lifetime  −  renewal margin
```

Pick the lifetime from the outage you must survive, not from a blog post. A product promising thirty days of autonomy cannot issue seven-day certificates.

**Revocation is a lifetime problem, not a list problem.** CRLs and OCSP both assume you can reach something, which is exactly the assumption this layer is not allowed to make. Revoke the intermediate to cut off a domain; let short service certificates expire; treat the device certificate as the one case where you accept a slower revocation and compensate with entitlement checks.

---

## Where the vault lives

The course plan flagged unsealing as having no clean answer: a box reboots unattended at 3am and must unseal without a human, while auto-unseal normally leans on a cloud KMS that an air-gapped site does not have.

It has a clean answer, and it falls straight out of the thesis:

> **If the appliance needs a vault to boot, the vault is not allowed to be unavailable — which contradicts the rule this layer is built on. So the appliance does not run a vault.**

The vault is central — it runs in this layer's cloud, alongside everything else here. The appliance holds its device certificate — in the TPM where there is one, never in the clear on disk — and caches short-lived credentials issued by its own domain's intermediate. Unsealing then stops being an edge problem and becomes an ordinary datacentre problem at the centre, where a KMS or an HSM is available and the question has a boring answer. **A customer running this layer privately inherits that problem rather than escaping it**, which is a real cost of the private-cloud option and belongs in the sales conversation, not just the architecture.

**The honest residue:** something must still survive first boot and be usable without a human. That is the device's private key, and hardware is the only place it genuinely belongs. This is much smaller than a whole vault at every site, but it is not nothing, and the module says so.

---

## Part A — Many networks, and who may be trusted across them

### Lesson 35 — The layer that is allowed to be down

- The thesis, and what it demands of every layer beneath it
- **The two jobs of this layer, kept apart:** it holds *authority* — trust, identity, entitlement — which every layer below caches and degrades on; and it supplies *capacity* — allocations a domain can be built from, which is a provisioning-time dependency and not a runtime one. Conflating them is what produces a cloud that cameras stop recording without
- What the orchestrating database actually holds: trust roots, device identities, people and the domains they can see, entitlements, inventory
- Why this is federation and not hierarchy — domains stay authoritative for their own operation
- **Not Nomad's federation.** The two planes, named explicitly
- Designing for absence: what each lower layer caches, for how long, and what it does when the cache expires

**Deliverable:** the federated schema, and a written table of what every layer below must cache and how it degrades.

---

### Lesson 36 — Many sites: regions, and one of them is a cloud

Moved here from М9, because "many sites" is where this module begins rather than where the appliance module ends.

- Regions are **fully independent** — they share no jobs, clients or state, and nothing replicates between them
- They are loosely coupled by a **gossip protocol**, so a job can be submitted to any region, or any region's state queried, transparently; requests are forwarded to the right regional servers
- Why "independent regions, loosely coupled" suits camera sites better than one stretched cluster: a site that loses its uplink keeps recording
- **A cloud region is just a region.** Rented servers on one provider network satisfy М11's definition of a domain exactly as a rack in a building does, and Nomad cannot tell the difference. That is the whole mechanism behind "edge, cloud or mixed" — there is no second system
- Namespaces and node pools for separating tenants and hardware classes — and node pools are how *edge* and *cloud* hardware are kept apart within one region when a deployment is mixed

**Deliverable:** two federated regions — one on local hardware, one on rented instances — each running the VMS, both reachable from one CLI.

---

### Lesson 37 — A Node that does not know where it is

The lesson that makes the deployment triangle real rather than a slide.

- **Deploy М10's Node three ways** — on a local server, on a rented instance, and split so a site's Nodes are local while the console and directory are not — and diff the artifacts. **They are identical.** If they are not, this lesson has found a bug in М10 or М11, which is the point of running it
- **The bandwidth calculation**, done before the demo rather than after: cameras × bitrate against the site's actual upstream, and the retention cost of storing in a cloud versus on a disk you own
- **What legitimately differs by placement**, and it is a short list: storage class and its cost curve, how the camera's stream reaches the Node, and who is paged when the hardware dies
- **What must never differ:** configuration ownership, the fencing epoch, the certificate chain, the update mechanism. A cloud deployment that quietly skips fencing because "the cloud does not lose servers" is the bug this lesson exists to prevent
- **Retrofitting М8.** The cloud VMS the course started with rented Kinesis for configuration *and* archive. Rebuild that shape on this layer — your Nodes, your object storage — and name what is genuinely lost by not renting: someone else's on-call rota, and a managed service's durability guarantee
- **The upload becomes conditional, and М9's spool comes back.** An on-prem Node has nobody to upload to; a cloud Node *is* the destination. Which means a cloud site has **no spool at all** — the camera streams over the internet to a Node that writes locally, and an uplink outage is not buffered, it is lost. Say this to the customer before they choose it, not after
- **So the camera becomes the buffer.** Edge recording to the camera's own SD card, backfilled over ONVIF when the link returns, is what turns "everything during the outage" into a stated number. It is the same publish-with-an-RPO shape as М9's spool and М11's configuration — with the buffer one hop further out, in a device the vendor does not control
- **Cost as a design input, stated honestly.** Egress pricing is what makes pure-cloud video expensive, and it is not a detail: a student who cannot estimate it will design a product that loses money per camera

**Deliverable:** the same Node running at the edge and in a cloud, both recording, both visible in one console — and a written bandwidth-and-cost estimate for a fifty-camera site that says which one it should be.

---

### Lesson 38 — Secure introduction

- The constraint М9 imposed: an identical image on every unit
- The four approaches and how each fails
- **BRSKI**: pledge, registrar, MASA, voucher, IDevID, LDevID, EST — and what running a MASA commits the vendor to
- TPM 2.0: sealing, attestation, and precisely what attestation does and does not prove
- Registration-with-approval as the shipped fallback, built properly: a queue, an audit trail, and an expiry on unapproved requests

**Deliverable:** a box enrolls from cold with nobody typing a secret, and the enrollment is auditable afterwards.

---

### Lesson 39 — A root, and an intermediate per domain

**М11 already runs a CA in every domain**, issuing short-lived certificates to its own Nodes from a self-signed root it hand-provisioned. This lesson does not introduce domain PKI. It replaces that root's *authority* with a delegated one, and demonstrates that nothing inside the domain has to change for it.

- CA hierarchy, built with `openssl` exactly as Lesson 17 built the RAUC chain
- **Why delegate to the domain** — the move that keeps routine issuance inside the site, and why the same move is impossible for a vault: **an intermediate is a bounded piece of the root's authority, transferred once a year; a secret has no such operation, and a copy in every domain is N places to steal it from**
- **Naming was already settled in М11** — the certificate names the Node, not the server it runs on. It is worth restating only because at fleet scope the temptation to name hosts returns
- Swapping the trust anchor under a running domain: cross-signing, or an overlap window, and why a flag day is not available to you
- Protecting the root: offline, and what a signing ceremony is for

**Deliverable:** a working chain, a service certificate issued *with the centre unplugged*, and М11's Node↔directory mTLS still up across the swap — the domain's issuance loop unchanged, only its trust anchor replaced.

---

### Lesson 40 — Lifetimes, renewal, and revocation that works offline

- The tension, and the split-by-job resolution above
- The arithmetic: tolerable outage equals lifetime minus renewal margin
- Renewal without downtime: overlapping validity, and reloading without dropping connections
- **Why CRL and OCSP are a poor fit here**, and what to do instead
- Clock skew, which breaks certificate validation in ways that look like everything else

**Deliverable:** simulate a thirty-day outage; the site keeps working. Then revoke a device and demonstrate it losing access on a schedule stated in advance.

---

### Lesson 41 — The debts, and how few of them a vault solves

Every earlier module left a marker. This lesson collects them — and the collecting is more interesting than it looks, because **most of them turn out not to need a vault at all.**

Start by auditing honestly, before introducing the tool:

| Debt | Where it was named | What actually resolves it |
|---|---|---|
| **М9's AWS credentials** | Lesson 19 | This layer replaces AWS with your own object storage. **Workload identity**, not a stored key |
| **М10's database password** | Lesson 20 | A *local* secret for a *local* Postgres. **Certificate or peer auth removes it entirely** |
| **М10's operator account** | Lesson 24 | Human identity. An **IdP** job — OIDC — not a vault's |
| **М12's per-Node credential** | Lesson 33 | Already replaced, in Lesson 39, by **mTLS with the LDevID** |
| **М12's self-signed domain CA** | Lesson 33 | Already replaced, in Lesson 39, by the **delegated intermediate** |
| **М10's camera credentials** | Lesson 20 | **A column key, and it must stay at the site** — see below |

Four of the six have non-vault answers and two were already paid two lessons ago. That is not an argument against secret management; it is the module being honest about a pattern this course has followed throughout — **most secrets exist because something was not given an identity.** Give the box an identity and the secret it was standing in for disappears.

- **What a vault genuinely does that a database cannot.** Postgres *stores* secrets; a vault *issues and revokes* them. Three specifics: the encryption key must not sit next to the data (a `pg_dump` and the app config travel together); **dynamic credentials** — a fresh database user per request with a one-hour TTL, dropped automatically — cannot be expressed as a table, because the lifecycle engine *is* the vault; and a stored `valid_until` cannot revoke anything by itself
- **What that leaves for OpenBao here**, stated as a scope rather than a tour: dynamic credentials for the services this layer runs, and the secrets a **multi-tenant** operator holds on behalf of many customers. If this layer is single-tenant and every machine has an identity, the honest answer is that **you may not need one** — and the lesson says so rather than teaching auth methods for their own sake
- **The one debt a central vault is the wrong answer to.** Camera credentials must be usable with the centre unreachable, so they cannot be fetched from anywhere. They stay at the site, encrypted with a key the database backup does not contain — the TPM, or a Nomad Variable. **Distance from the data is the security property, not the product name**
- **Where the vault lives**, from the section above, and why that dissolves the unsealing problem
- Machine identity: how a service proves who it is to get a secret, now that the box has an LDevID to speak for it

**Deliverable:** a grep across М9–М12 that finds no hand-provisioned secret left — and a written table saying, for each one, whether a vault was what removed it. **If fewer than half needed a vault, that is the correct result and the lesson's actual conclusion.**

---

## Part B — Operating the fleet

### Lesson 42 — People, roles, and scope across domains

- An operator who can watch three sites and administer one — which is the half of this that only exists above a domain
- **Authorization is deliberately scattered, and М11 explains why.** Grants live in each Node so they can be enforced with the domain unreachable. What this module adds is not a central check but **one Alice**: a federated identity the grants refer to, so she is not N separate records that can disagree about who she is
- Replacing **both** of М12 Lesson 33's stand-ins with one thing: the hand-provisioned per-Node credential, and the **hand-provisioned domain token issuer**. The Nodes already verify a signature against a public key rather than storing anybody's password — so what changes is *whose key*, and nothing about how a Node authenticates a person changes at all. **That is the payoff for delegating an authority rather than distributing a secret**, and it is the same shape as the CA swap in Lesson 39
- Why identity for people and identity for machines share a trust root but not a lifecycle
- Delegated administration: the customer's own administrator, and what the vendor can and cannot see

**Deliverable:** one identity that authenticates against three Nodes in two domains, with a test that a scoped operator cannot read a domain they were not granted — and that revoking the identity ends access on every Node within the window Lesson 33 states.

---

### Lesson 43 — Inventory: reported, never commanded

- М10's rule at fleet scope: inventory is *observation*, and nothing in it is authoritative over a device
- What a box reports, how often, and how much of a thin uplink that may consume
- Reconciling inventory against entitlement — what you have versus what you are licensed for, and which one wins when they disagree
- The divergence idea from М12 Lesson 30, applied to a fleet: something running that inventory does not know about is a gap in the model

**Deliverable:** an inventory view across domains, and a report of everything it cannot account for.

---

### Lesson 44 — Version skew is the normal state

- A fleet on mixed versions is not a failure to be eliminated. You cannot update everything at once, so the only question is whether the design admits it
- **The compatibility rule:** the controller-to-worker contract must tolerate N−1, and preferably N−2. This is where М11's opaque config and revision ordering pay off — a controller that never parses worker config cannot be broken by a worker that is a version behind
- Rollout as a population operation: canary, rings, and a halt condition that fires automatically
- Schema migration across a skewed fleet: expand, migrate, contract — never a breaking change in one step

**Deliverable:** run a controller against workers at two versions simultaneously, and a rollout that halts itself on a failing canary.

---

### Lesson 45 — hawkBit, and closing both update planes

The capstone.

- **Eclipse hawkBit** for OS update delivery: pull-based, which partly restores what М9 gave up choosing Nomad Pack over Fleet
- RAUC's hawkBit integration, and why the OS plane is the one that most needs pull — a site on a flaky link must be able to catch up by itself
- The two update planes from М9, now finally with a control plane spanning sites
- What is still not solved, said out loud rather than left as an impression

**Deliverable:** a box enrolls, joins its domain, records, updates its operating system, and reports its version — with nobody on site, and the student able to narrate every step.

---

## Verification plan

**Track 1 — verified in the authoring sandbox.** More than expected, because PKI is files and `openssl`, and М9 already proved that works here:

- The whole CA hierarchy: root, per-domain intermediates, issuance, chain validation, and proving that a certificate from the wrong intermediate is rejected
- Lifetime and expiry behaviour, including the thirty-day outage, by issuing short-lived certificates and moving time rather than waiting
- Revocation semantics, renewal with overlapping validity, and clock-skew failures
- OpenBao in a container: auth methods, policies, dynamic credentials, leases — and the audit in Lesson 41 that decides how much of it this product needs
- Inventory reconciliation, the entitlement comparison, and the N−1 compatibility tests — all ordinary software, testable against fake workers in the style of Lessons 11–15

**Track 2 — needs real hardware.** Three things: **TPM 2.0**, which cannot be faked in any way worth teaching; **two federated Nomad regions** for Lesson 36; and hawkBit driving real appliances. BRSKI can be walked through end to end with a simulated MASA, but a student without a TPM is reading rather than running Lesson 38's second half, and the lesson should say which paragraph that starts at.

---

## Open questions

1. **Does the vendor run a MASA?** BRSKI is unimplementable without one, and it is a permanent operational commitment. A business decision the module can frame but not take.
2. **Where does this layer run** — vendor cloud, customer datacentre, or one per customer? It decides whether it is multi-tenant, which changes the schema and most of Lesson 42. Now sharper than before: a layer that only holds authority can be small and shared; a layer that also supplies capacity is a hosting business, with the margins, on-call and compliance surface of one.
3. **Is the vendor in the hosting business, or the software business?** "Provide allocations in the cloud" is the sentence that decides it. Reselling compute at a markup, running someone else's video through your egress, and being paged when an instance dies are commitments of a different kind from shipping software — and the module can frame the choice but not take it.
4. **Does a cloud Node get the same fencing story?** It must, and the module asserts it, but the failure mode differs: a partitioned rented instance may keep running longer than a powered-off server, which makes the epoch *more* load-bearing in the cloud, not less. Worth testing rather than assuming.
5. **Are air-gapped sites a supported configuration?** A site that never reaches the centre cannot renew an intermediate, and the thirty-day answer becomes a one-year answer or a manual one.
6. **М14's position**, still open from the course plan and sharpened twice over: this is now an eleven-lesson module, and three of its lessons lean on instrumentation it has not taught — the thirty-day outage cannot be *demonstrated* without metrics, and a self-halting canary is an alert rule. The recorded counter-argument ("never log a secret" is easier once students know what a secret is) is one rule, teachable in a sentence.
7. **Cross-domain archive search.** Search over footage spanning domains is the obvious next thing this layer enables — and a mixed deployment makes it arrive sooner, because one customer now routinely has footage in two places. Currently out of scope. Worth deciding deliberately rather than by omission.

---

## Sources

- [RFC 8995 — Bootstrapping Remote Secure Key Infrastructure (BRSKI)](https://datatracker.ietf.org/doc/html/rfc8995) — pledge, registrar, MASA, voucher, IDevID and LDevID; zero-touch bootstrap without pre-shared keys or bare trust-on-first-use
- [RFC 8995 at the RFC Editor](https://www.rfc-editor.org/info/rfc8995/) — the citable record
- [`consul-and-openbao.md`](consul-and-openbao.md) — why mTLS comes from the PKI rather than a service mesh
- [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) and [М11's design](../М11_ClusterVMS/module-design.md) — the contracts that make version skew survivable

*Written 5 September 2026.*
