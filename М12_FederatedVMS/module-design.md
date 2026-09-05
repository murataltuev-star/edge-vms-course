# М12_FederatedVMS — Module Design

**The first layer that is allowed to be unavailable.**

Edge, Node and Domain each widened the scope of truth and added a new class of disagreement. This module adds the last one: things that must be true **above** any single domain — who a box is, who a person is, what a customer is entitled to, and what the fleet actually consists of.

It is also the first layer that may be switched off without the product stopping, and that constraint shapes every decision in it.

> **Scope note.** [`COURSE-PLAN.md`](../COURSE-PLAN.md) originally had these as two modules three apart: М12 for secrets and PKI, М14 for device management. They asked the same question twice — *"how does a machine prove who it is to get its first secret?"* and *"how does a box join and get an identity without someone typing secrets into it?"* — and neither owned it. They are merged here. The seven-layer model had identity as layer 5 and device management as layer 7; building it revealed they are one layer, and enrollment is where they meet.

> **A word that is already taken.** М9 Lesson 22 teaches Nomad *federation* — regions joined by gossip. That is the workload plane. This is the product plane, across domains. Two planes again, exactly as М9's OS-versus-workload distinction, and the module says so on its first page so students do not merge them.

---

## The thesis

> **Everything below this layer must keep working when this layer is unreachable.**

A site records video whether or not the centre answers. The node converges. The domain places cameras. The federation supplies identity, trust and entitlement — and every consumer of those must **cache them and degrade on a grace period**, never block on them.

This is what makes the arrangement a federation rather than a hierarchy, and it is not a nicety. A VMS whose cameras stop recording because a certificate service is down has failed at the only job it has.

The rule produces three consequences that this module spends nine lessons on:

| Because the centre may be unreachable… | …the design must |
|---|---|
| A box cannot ask permission to boot | carry an identity it can prove offline |
| A service cannot fetch a certificate on demand | renew from something inside its own domain |
| An appliance cannot consult a vault to start | not need a vault to start |

---

## The demo it is built backwards from

A box arrives at a site in a cardboard carton. Nobody types a secret into it. It is powered on and given a network, and within minutes it has proved who it is, received a certificate, fetched its configuration, joined its domain and started recording.

Then the uplink is cut for **thirty days**. It keeps recording. Service certificates renew from inside the domain. Entitlement holds on its grace period. When the link returns, inventory catches up, version skew is reported, and nothing was lost.

Finally the box is marked stolen, and loses access on a schedule the student can state in advance.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| Availability | **The federated layer may be down** | Stated first because everything else follows from it. |
| Device identity | **Hardware-rooted where possible, approved-registration otherwise** | The A/B image is byte-identical on every appliance, so nothing device-specific can live in it. |
| Bootstrap model | **BRSKI as the reference, TOFU-with-approval as the shipped fallback** | The standard exists and is worth teaching. Whether the vendor runs the service it requires is a business decision, not a technical one. |
| CA topology | **Offline root, one intermediate per domain** | The structural move that makes offline tolerance possible: routine issuance never leaves the site. |
| Revocation | **Short lifetimes, not revocation lists** | CRL and OCSP both assume you can reach something. At the edge you frequently cannot. |
| Vault placement | **Central. The appliance does not run one** | Resolves the unsealing problem by dissolving it — see below. |
| Inventory | **Reported, never commanded** | М10's rule at fleet scope: desired state is persisted, actual state is observed. |
| Version skew | **A normal operating state, not a fault** | You cannot update a fleet atomically, so the contract must tolerate mixed versions by design. |
| Service mesh | **No Consul** | See [`consul-and-openbao.md`](consul-and-openbao.md). |

---

## Prerequisites

- **М9 Lesson 19** — credentials are provisioned at commissioning, never baked into an image that ships identically to every device. This module finally answers *how*.
- **М9 Lesson 17** — the RAUC signing chain, built with real `openssl`. The PKI lessons here are the same skill, one scope up.
- **М10 Lesson 25** — the hand-provisioned database password, marked temporary. Cashed in at Lesson 39.
- **М11 Lesson 34** — the deliberately unauthenticated API. Also cashed in at Lesson 39.
- **М11 entire** — opaque config and revision ordering are what make version skew survivable, and Lesson 42 collects on that.

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
| Service-to-service, inside a domain | Hours to days | The domain's own intermediate | **Never** |
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

The vault is central. The appliance holds its device certificate — in the TPM where there is one, never in the clear on disk — and caches short-lived credentials issued by its own domain's intermediate. Unsealing then stops being an edge problem and becomes an ordinary datacentre problem at the centre, where cloud KMS and HSMs are available and the question has a boring answer.

**The honest residue:** something must still survive first boot and be usable without a human. That is the device's private key, and hardware is the only place it genuinely belongs. This is much smaller than a whole vault at every site, but it is not nothing, and the module says so.

---

## Part A — Identity

### Lesson 35 — The layer that is allowed to be down

- The thesis, and what it demands of every layer beneath it
- What the federated database actually holds: trust roots, device identities, people and the domains they can see, entitlements, inventory
- Why this is federation and not hierarchy — domains stay authoritative for their own operation
- **Not Nomad's federation.** The two planes, named explicitly
- Designing for absence: what each lower layer caches, for how long, and what it does when the cache expires

**Deliverable:** the federated schema, and a written table of what every layer below must cache and how it degrades.

---

### Lesson 36 — Secure introduction

- The constraint М9 imposed: an identical image on every unit
- The four approaches and how each fails
- **BRSKI**: pledge, registrar, MASA, voucher, IDevID, LDevID, EST — and what running a MASA commits the vendor to
- TPM 2.0: sealing, attestation, and precisely what attestation does and does not prove
- Registration-with-approval as the shipped fallback, built properly: a queue, an audit trail, and an expiry on unapproved requests

**Deliverable:** a box enrolls from cold with nobody typing a secret, and the enrollment is auditable afterwards.

---

### Lesson 37 — A root, and an intermediate per domain

- CA hierarchy, built with `openssl` exactly as Lesson 17 built the RAUC chain
- **Why delegate to the domain** — the move that keeps routine issuance inside the site
- Naming: certificates for services that move between nodes, and why the name must not be the hostname
- Protecting the root: offline, and what a signing ceremony is for

**Deliverable:** a working chain, and a service certificate issued *with the centre unplugged*.

---

### Lesson 38 — Lifetimes, renewal, and revocation that works offline

- The tension, and the split-by-job resolution above
- The arithmetic: tolerable outage equals lifetime minus renewal margin
- Renewal without downtime: overlapping validity, and reloading without dropping connections
- **Why CRL and OCSP are a poor fit here**, and what to do instead
- Clock skew, which breaks certificate validation in ways that look like everything else

**Deliverable:** simulate a thirty-day outage; the site keeps working. Then revoke a device and demonstrate it losing access on a schedule stated in advance.

---

### Lesson 39 — Secrets, and the debts from three modules

Every earlier module left a marker. This lesson collects them all.

- М9's hand-provisioned AWS credentials; М10's database password; М11's unauthenticated API — each marked temporary where it appeared, each replaced here
- OpenBao: auth methods, policies, dynamic credentials, leases
- **Where the vault lives**, from the section above, and why that dissolves the unsealing problem
- Machine identity: how a service proves who it is to get a secret, now that the box has an LDevID to speak for it

**Deliverable:** М11's API authenticated by real identities, and a grep across М9–М11 that finds no hand-provisioned secret left.

---

## Part B — Operating the federation

### Lesson 40 — People, roles, and scope across domains

- An operator who can watch three sites and administer one
- Authorization at the boundary rather than scattered through the domain — М11 deliberately left this out, and this is where it belongs
- Why identity for people and identity for machines share a trust root but not a lifecycle
- Delegated administration: the customer's own administrator, and what the vendor can and cannot see

**Deliverable:** a role model enforced at the API, with a test that a scoped operator cannot read a domain they were not granted.

---

### Lesson 41 — Inventory: reported, never commanded

- М10's rule at fleet scope: inventory is *observation*, and nothing in it is authoritative over a device
- What a box reports, how often, and how much of a thin uplink that may consume
- Reconciling inventory against entitlement — what you have versus what you are licensed for, and which one wins when they disagree
- The divergence idea from М11 Lesson 33, applied to a fleet: something running that inventory does not know about is a gap in the model

**Deliverable:** an inventory view across domains, and a report of everything it cannot account for.

---

### Lesson 42 — Version skew is the normal state

- A fleet on mixed versions is not a failure to be eliminated. You cannot update everything at once, so the only question is whether the design admits it
- **The compatibility rule:** the controller-to-worker contract must tolerate N−1, and preferably N−2. This is where М11's opaque config and revision ordering pay off — a controller that never parses worker config cannot be broken by a worker that is a version behind
- Rollout as a population operation: canary, rings, and a halt condition that fires automatically
- Schema migration across a skewed fleet: expand, migrate, contract — never a breaking change in one step

**Deliverable:** run a controller against workers at two versions simultaneously, and a rollout that halts itself on a failing canary.

---

### Lesson 43 — hawkBit, and closing both update planes

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
- OpenBao in a container: auth methods, policies, dynamic credentials, leases
- Inventory reconciliation, the entitlement comparison, and the N−1 compatibility tests — all ordinary software, testable against fake workers in the style of Lessons 11–15

**Track 2 — needs real hardware.** Genuinely two things: **TPM 2.0**, which cannot be faked in any way worth teaching, and hawkBit driving real appliances. BRSKI can be walked through end to end with a simulated MASA, but a student without a TPM is reading rather than running Lesson 36's second half, and the lesson should say which paragraph that starts at.

---

## Open questions

1. **Does the vendor run a MASA?** BRSKI is unimplementable without one, and it is a permanent operational commitment. A business decision the module can frame but not take.
2. **Where does the federated database live** — vendor cloud, customer datacentre, or one per customer? It decides whether this layer is multi-tenant, which changes the schema and most of Lesson 40.
3. **Are air-gapped sites a supported configuration?** A site that never reaches the centre cannot renew an intermediate, and the thirty-day answer becomes a one-year answer or a manual one.
4. **М13's position**, still open from the course plan and sharpened by the merge: this is now a nine-lesson module, and observability might reasonably come before it rather than after. The counter-argument stands — "never log a secret" is easier to teach once students know what a secret is.
5. **Cross-domain archive search.** Federated search over footage is the obvious next thing this layer enables and is currently out of scope. Worth deciding deliberately rather than by omission.

---

## Sources

- [RFC 8995 — Bootstrapping Remote Secure Key Infrastructure (BRSKI)](https://datatracker.ietf.org/doc/html/rfc8995) — pledge, registrar, MASA, voucher, IDevID and LDevID; zero-touch bootstrap without pre-shared keys or bare trust-on-first-use
- [RFC 8995 at the RFC Editor](https://www.rfc-editor.org/info/rfc8995/) — the citable record
- [`consul-and-openbao.md`](consul-and-openbao.md) — why mTLS comes from the PKI rather than a service mesh
- [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md) and [М11's design](../М11_DomainVMS/module-design.md) — the contracts that make version skew survivable

*Written 5 September 2026.*
