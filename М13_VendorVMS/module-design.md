# М13_VendorVMS — Module Design

**The side of the boundary the product must survive, not depend on.**

Every module before this one built a scope of the product: a box, a Node, a cluster, a domain. This one builds none. The domain is the top of the product — one customer is one domain, and a domain can be as large as their whole estate — so what sits above it is not a layer of the system. **It is the vendor**: a different organisation, with its own concerns, on the far side of a boundary the product was designed to work across in only one direction.

That is why this module changed kind rather than just name. Its earlier drafts were a fifth scope — a federated layer holding a root CA, an identity provider, a vault, a fleet inventory and rented capacity. Item by item, each turned out to be either something the domain can do for itself or something the vendor does *across customers*. What remained is this module, and it is short on purpose.

> **Scope note.** Formerly *VendorVMS*, and before that *FederatedVMS*. Both names described a layer that provided capacity or trust to domains from above. The domain turned out not to need either — it provisions its own clusters from the customer's cloud account (М12 Lesson 37) and is its own trust root (М12 Lesson 36). What a vendor genuinely does is vouch for hardware, sell entitlements, publish updates, and optionally rent capacity; none of it is a runtime dependency, and the module is arranged to prove that.

---

## The thesis

> **The product must work with the vendor unreachable — or gone.**

Not a resilience nicety. It is the vendor-independence property that enterprise security buyers ask for by name, the reason an air-gapped deployment is a first-class configuration rather than an exception, and the only honest answer to *what happens to my cameras if you go out of business.* Everything the vendor runs is consumed by the domain through a cache with a grace period, and the grace period is a number in the datasheet.

The module is the mirror image of the course so far. М9–М12 asked *what must the product do*; this one asks **what may the vendor do, and what must it never be able to do**:

| The vendor may | The vendor must never be able to |
|---|---|
| vouch that a box is genuinely its hardware | join a box to a customer's domain on its own |
| issue an entitlement | stop recording by withholding one |
| publish an OS bundle | push it onto an appliance directly |
| see aggregated inventory the customer chose to report | read configuration, footage, or credentials |
| rent a cluster | hold the customer's trust root, or impersonate the domain |

The right-hand column is a list of things earlier drafts of this course would have let the vendor do.

---

## The demo it is built backwards from

A customer's domain has been running for a year. Then, on a date the student picks, **the vendor disappears** — MASA, licence server, update server, everything.

For thirty days: recording continues, operators log in, cameras are added within the entitlement's grace period, certificates renew, a server fails over. Nothing that was already working stops. What *cannot* happen is enumerated, not implied: no new appliance enrolls with BRSKI (approval still works), no new OS bundle arrives, and on day thirty-one the entitlement cache reaches its stated floor and the product degrades exactly as Lesson 34 wrote down.

Then the vendor comes back, and the student shows what caught up and what was never affected.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| The vendor's position | **Outside the product, across a one-way boundary** | One customer is one domain; a domain is its own top. The vendor is a counterparty, not a layer. |
| Trust | **The vendor holds no root over any customer** | A vendor-held root that signs the customer's CA can impersonate the customer's domain. The single cryptographic relationship is the MASA voucher, said once at enrollment. |
| Entitlement | **Issued by the vendor, cached and degraded by the domain** | What degrades is the domain's decision (М12 L34); the vendor only supplies the number. Withholding it never stops recording. |
| Updates | **Published by the vendor, pulled by the domain's own update server** | The vendor never reaches an appliance. An air-gapped domain is updated by carrying a bundle to its hawkBit. |
| Inventory | **Reported by the domain, at the customer's discretion** | The vendor sees aggregates for support, never configuration or footage. |
| Rented capacity | **A commercial option, not an architecture** | A rented cluster is a cluster (М12 L37). Whether the vendor is the landlord is the hosting-business question, kept separate from whether the product works. |
| Secrets | **OpenBao only where the vendor is multi-tenant** | A single customer's domain needs no vault (М12 uses Variables). A vendor holding many customers' cloud credentials does. |
| Fleet rollout | **Across customers, canary-first, halting automatically** | Version skew within a domain is М12's N−1 contract. Across the fleet it is the vendor's operational problem, not the product's. |

---

## Prerequisites

- **М12 entire** — especially Lesson 34 (the domain's update server and entitlement cache), Lesson 35 (enrollment, and where the MASA sits), Lesson 36 (the domain as its own root).
- **М9 Lesson 17** — the RAUC signing chain. Publishing a bundle is signing it, and the vendor's bundle-signing key is the one secret in this module that genuinely is the vendor's.
- **М14** would help and is not required: Lesson 40's canary halt condition is an alert rule, and a student who has met alerting reads it faster.

---

## Lessons

*Four lessons, each about one thing the vendor does — and what the product must not need from it.*

### Lesson 38 — The vendor's side, and the one-way boundary

- The thesis, and the two-column table above, defended item by item
- **Why one customer is one domain**, and therefore why nothing above the domain is the product's — the argument that dissolved the earlier drafts, retold so students can reproduce it rather than accept it
- **What the vendor can see**, exactly: aggregated inventory the domain chose to report, entitlement consumption, bundle versions. And what it cannot, by construction: configuration, footage, credentials, the trust root
- **The grace-period contract**, stated once for everything: entitlement, MASA availability, update availability. Each has a number the domain caches for, and the datasheet carries all three
- The private-vendor case: a customer who *is* their own vendor — a large integrator, a government — and what changes (nothing in the product; everything in who runs a MASA)

**Deliverable:** a written boundary document — every interaction between domain and vendor, its direction, its cache lifetime, and what degrades when it lapses.

---

### Lesson 39 — MASA, and what running one commits you to

- **The voucher**, from the vendor's side: a signed statement that *this* IDevID is *my* hardware and may trust *that* registrar. The one cryptographic thing a customer ever needs from the vendor, and it is said once
- **Device → domain routing.** A pledge has no domain yet. Something above must know serial 4471 belongs to customer X — an entitlement lookup, and the only reason enrollment touches the vendor at all
- **The commitment, stated plainly:** a service with an availability requirement and a signing key that must never leak, **for the lifetime of every appliance ever shipped.** Ten years after the last unit sells, the MASA is still on call
- **What happens when it is not:** the registration-with-approval fallback from М12 Lesson 35 is what a customer uses when the MASA is unreachable — and it is also the answer to *what if the vendor is gone.* The ladder from М12 read the other way round
- Manufacturing: installing an IDevID at the factory, and the secret database that implies

**Deliverable:** a simulated MASA issues vouchers to a domain's registrar; then the MASA is switched off and a new box still joins by approval, with the audit trail showing which path it took.

---

### Lesson 40 — Entitlement, publishing, and rollout across customers

- **Issuing entitlement:** what a licence actually encodes — camera count, features, a validity window — and why it is signed rather than looked up, so the domain can verify it offline for the whole grace period
- **Publishing a bundle:** signing with the vendor's key (М9 Lesson 17's chain, from the other side), and pushing to each domain's update server — never to a box
- **Rollout as a population operation:** canary customers, rings, and **a halt condition that fires automatically** — an alert rule, and the reason М14 would have been useful first
- **Version skew across the fleet is the normal state.** You cannot update every customer at once, so the controller–worker contract tolerates N−1 and preferably N−2 — which is where М12's opaque config and revision ordering pay off across the fleet, not just within a domain
- Schema migration across a skewed fleet: expand, migrate, contract — never a breaking change in one step, because some domain is always a version behind

**Deliverable:** a rollout to three simulated customer domains that halts itself when the canary's `camera_silent_seconds` rises — and a bundle that reaches an air-gapped domain by hand and installs identically.

---

### Lesson 41 — The hosting business, support, and what a vault is actually for

- **Rented capacity as a commercial offer.** A rented cluster is just a cluster (М12 L37). What changes when the vendor is the landlord: on-call for hardware the customer does not own, egress costs for video that is not yours, and a compliance surface. **The module frames this decision and does not take it**
- **Multi-tenancy, if you are hosting.** One vendor, many customers' cloud credentials and integration secrets — and this, finally, is what **OpenBao** is for: dynamic credentials and secrets held on behalf of many tenants. Everything else the course once assigned to a vault was removed by giving machines identities
- **Support inventory:** what a domain reports upward, at the customer's discretion; what the vendor's support view can and cannot answer; and the divergence idea from М12 Lesson 30 across customers — something running that no domain reported is a gap in the model
- **What the vendor must never be able to reach**, restated as a test: a support engineer with full vendor access attempts to read a customer's configuration, footage, or credentials, and the attempt fails by construction rather than by policy

**Deliverable:** the vendor-side view across three domains, and a written statement of every question it cannot answer — plus the test that a full-access vendor account cannot read a customer's camera password.

---

## Verification plan

**Track 1 — verified in the authoring sandbox.** Nearly all of it: a simulated MASA is an HTTP service signing vouchers; entitlement is a signed document verified offline; rollout is a state machine against fake domains; the boundary document is prose. The vendor-disappears demo needs only the domain from М12 and a switch.

**Track 2 — needs real hardware.** An IDevID installed at manufacture, and a TPM — neither can be simulated in a way worth teaching. A student without them reads Lesson 39's first half and runs the approval fallback instead.

---

## Open questions

1. **Is the vendor in the hosting business?** The sentence *provide allocations in the cloud* decides it, and the module frames the choice without taking it.
2. **Does the vendor run a MASA at all?** Without one, BRSKI is unimplementable and approval is the only enrollment path — which many customers prefer anyway.
3. **How much inventory does a customer report?** The default should be minimal and the datasheet should say what it is; a vendor that asks for more has to say why.
4. **Should М14 come before this module?** Lesson 40's halt condition is an alert rule. The case for observability preceding the vendor module is at least as strong as the case for it preceding the old orchestration one.

---

## Sources

- [RFC 8995 — BRSKI](https://datatracker.ietf.org/doc/html/rfc8995) — pledge, registrar, MASA, voucher, IDevID and LDevID; the registrar belongs to the domain, the MASA to the manufacturer
- [Eclipse hawkBit](https://eclipse.dev/hawkbit/) — pull-based update delivery; the vendor publishes, the domain's server serves
- [`consul-and-openbao.md`](../М12_DomainVMS/consul-and-openbao.md) — why mTLS comes from the PKI, and what is left for a vault
- [М12's design](../М12_DomainVMS/module-design.md) — the domain as its own root, and the contracts that let a fleet run on mixed versions

*Written 5 September 2026 as FederatedVMS; became VendorVMS on 7 September; became VendorVMS the same day, when the layer it described turned out not to exist.*
