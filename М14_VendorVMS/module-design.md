# М14_VendorVMS — Module Design

**The side of the boundary the product must survive, not depend on.**

Every module before this one built a scope of the product: a box, a Node, a cluster, a domain. This one builds none. The domain is the top of the product — one customer is one domain, and a domain can be as large as their whole estate — so what sits above it is not a layer of the system. **It is the vendor**: a different organisation, with its own concerns, on the far side of a boundary the product was designed to work across in only one direction.

That is why this module changed kind rather than just name. Its earlier drafts were a fifth scope — a federated layer holding a root CA, an identity provider, a vault, a fleet inventory and rented capacity. Item by item, each turned out to be either something the domain can do for itself or something the vendor does *across customers*. What remained is this module, and it is short on purpose.

> **Scope note.** Formerly *OrchestratedVMS*, and before that *FederatedVMS*. Both names described a layer that provided capacity or trust to domains from above. The domain turned out not to need either — it provisions its own clusters from the customer's cloud account (М12 Lesson 8) and is its own trust root (М12 Lesson 7). What a vendor genuinely does is vouch for hardware, run the licence system, publish updates, and optionally rent capacity; none of it is a runtime dependency, and the module is arranged to prove that.

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

For thirty days: recording continues, operators log in, cameras are added within the entitlement's grace period, certificates renew, a server fails over. Nothing that was already working stops. What *cannot* happen is enumerated, not implied: no new appliance enrolls with BRSKI (approval still works), no new OS bundle arrives, and on day thirty-one the licence runs out of grace and the product degrades exactly as М12 Lesson 5 wrote down — and Lesson 3 here explains why that floor was set where it was.

Then the vendor comes back, and the student shows what caught up and what was never affected.

---

## Decisions taken

| Decision | Choice | Why |
|---|---|---|
| The vendor's position | **Outside the product, across a one-way boundary** | One customer is one domain; a domain is its own top. The vendor is a counterparty, not a layer. |
| Trust | **The vendor holds no root over any customer** | A vendor-held root that signs the customer's CA can impersonate the customer's domain. The single cryptographic relationship is the MASA voucher, said once at enrollment. |
| Entitlement | **Issued by the vendor, cached and degraded by the domain** | What degrades is the domain's decision (М12 L5); the vendor only supplies the number. Withholding it never stops recording. |
| The licence system | **The vendor's system of record, with one signing key; the licence itself a signed document verified offline** | The domain must verify without calling anyone (Lesson 3). The vendor's database answers *who owns serial 4471* and *what did customer X buy*; the signed document is the only part that travels. |
| What a licence binds to | **The domain, by its stable domain id — never a server, never a box** | A Node is not a server (М11), so a licence pinned to hardware would break on the first failover. Binding to the root's fingerprint would tie a licence to a rotation drill (М12 L7). The domain id is the one thing that outlives both. |
| Where the count is enforced | **At admission, by placement — eventually consistent** | No consistent domain-wide counter exists (the directory of directories cannot be consistent, М12 L1). Refusing a *new* camera is safe to get slightly wrong; stopping an existing one is not, so runtime never enforces. |
| Licence revocation | **None. Lifetimes only** | The same rule as certificates (М12 L7): revocation assumes you can reach something. A licence carries a validity window and a grace period; a perpetual licence carries neither and is the honest offer for *what if you are gone*. |
| Updates | **Published by the vendor, pulled by the domain's own update server** | The vendor never reaches an appliance. An air-gapped domain is updated by carrying a bundle to its hawkBit. |
| Inventory | **Reported by the domain, at the customer's discretion** | The vendor sees aggregates for support, never configuration or footage. |
| Rented capacity | **A commercial option, not an architecture** | A rented cluster is a cluster (М12 L8). Whether the vendor is the landlord is the hosting-business question, kept separate from whether the product works. |
| Secrets | **OpenBao only where the vendor is multi-tenant** | A single customer's domain needs no vault (М12 uses Variables). A vendor holding many customers' cloud credentials does. |
| Fleet rollout | **Across customers, canary-first, halting automatically** | Version skew within a domain is М12's N−1 contract. Across the fleet it is the vendor's operational problem, not the product's. |

---

## Prerequisites

- **М12 entire** — especially М12 Lesson 5 (the domain's update server and entitlement cache — the consuming side of Lesson 3), М12 Lesson 6 (enrollment, and where the MASA sits), М12 Lesson 7 (the domain as its own root, and the lifetime rule the licence reuses).
- **М9 Lesson 2** — the RAUC signing chain. Publishing a bundle is signing it, and the vendor's bundle-signing key is the one secret in this module that genuinely is the vendor's.
- **М13** — the vendor-disappears demo is measured with its instruments; `entitlement_seconds_remaining` is one of its gauges, and Lesson 4's canary halt condition is М13 Lesson 3's alert rule.

---

## Lessons

*Five lessons, each about one thing the vendor does — and what the product must not need from it.*

### Lesson 1 — The vendor's side, and the one-way boundary

- The thesis, and the two-column table above, defended item by item
- **Why one customer is one domain**, and therefore why nothing above the domain is the product's — the argument that dissolved the earlier drafts, retold so students can reproduce it rather than accept it
- **What the vendor can see**, exactly: aggregated inventory the domain chose to report, licence consumption, bundle versions. And what it cannot, by construction: configuration, footage, credentials, the trust root
- **The grace-period contract**, stated once for everything: entitlement, MASA availability, update availability. Each has a number the domain caches for, and the datasheet carries all three
- The private-vendor case: a customer who *is* their own vendor — a large integrator, a government — and what changes (nothing in the product; everything in who runs a MASA)

**Deliverable:** a written boundary document — every interaction between domain and vendor, its direction, its cache lifetime, and what degrades when it lapses.

---

### Lesson 2 — MASA, and what running one commits you to

- **The voucher**, from the vendor's side: a signed statement that *this* IDevID is *my* hardware and may trust *that* registrar. The one cryptographic thing a customer ever needs from the vendor, and it is said once
- **Device → domain routing.** A pledge has no domain yet. Something above must know serial 4471 belongs to customer X — a lookup in the licence system's database (Lesson 3), and the only reason enrollment touches the vendor at all
- **The commitment, stated plainly:** a service with an availability requirement and a signing key that must never leak, **for the lifetime of every appliance ever shipped.** Ten years after the last unit sells, the MASA is still on call
- **What happens when it is not:** the registration-with-approval fallback from М12 Lesson 6 is what a customer uses when the MASA is unreachable — and it is also the answer to *what if the vendor is gone.* The ladder from М12 read the other way round
- Manufacturing: installing an IDevID at the factory, and the secret database that implies

**Deliverable:** a simulated MASA issues vouchers to a domain's registrar; then the MASA is switched off and a new box still joins by approval, with the audit trail showing which path it took.

---

### Lesson 3 — The licence system

*The vendor's side of М12 Lesson 5. That lesson decided what the domain does when the licence lapses; this one decides everything upstream of that — what a licence is, who it names, how it gets there, and what the vendor keeps to be able to issue the next one.*

- **What the vendor runs**, and it is smaller than the word *system* suggests: a **database** (customers, what each bought, which appliance serials are theirs, which domain id each customer's installation carries), an **issuer** (one signing key, in an HSM, that turns a row of that database into a signed document), and a **consumption view** (whatever domains chose to report). The database is an ordinary multi-tenant Postgres at the vendor — the first ordinary database the course has seen since М8, and it is fine here because *nothing in a customer's domain depends on reaching it*
- **What a licence is:** a signed document, not a lookup. Domain id; camera count; features (which detectors, which integrations); `not_before` / `not_after`; the grace period; a serial and the issuer's key id. Signed with the vendor's **licence key** — a second key beside the bundle-signing key of М9 Lesson 2, kept separate because losing one must not mean losing the other. The public half ships inside the product like a RAUC keyring, and rotates the same way: through a bundle, with the old key trusted until every licence it signed has expired
- **What it binds to — the domain id, and nothing physical.** The course has spent three modules making sure a Node is not a server; a licence pinned to a serial or a MAC would undo that on the first failover. Binding to the root certificate's fingerprint is the next temptation, and it ties every licence to М12 Lesson 7's rotation drill — a vendor that is gone could never reissue. So the domain gets a **stable id at creation**, recorded in its Variables and carried in its root certificate's subject, and the licence names that. A copied licence in a second domain with the same id is *not prevented*, and the lesson says so plainly: offline licensing keeps honest customers honest, and the copy is found in the consumption view (Lesson 5), commercially, not by the product refusing to record
- **How it arrives.** The same way everything from the vendor arrives — **pulled**. A licence is published to the domain's update server (М12 Lesson 5's hawkBit) as an artifact with no OS payload, fetched on the same poll as bundles, and carried in by hand on an air-gapped domain. No licence server is ever called at runtime; there is no *phone home* to fail. Renewal is the next document appearing on the same path
- **Where the domain keeps it, and who checks it.** The document is small and must be consistent, so it lives in the **domain cluster's Nomad Variables** and is republished to every cluster's Variables through the one-way configuration flow. Every Node verifies the signature *itself*, against the shipped public key — the relay is trusted to deliver, never to vouch. A `licensed` condition on each object (М9 Lesson 9 built the slot for it, with nothing wired in; this is where the wire goes)
- **Where the count is enforced — and where it must not be.** There is no consistent domain-wide counter; М12 Lesson 1 proved the directory of directories cannot be one. So the count is checked **at admission, by placement**: a new camera is refused a cluster when the reported total is at the limit. That check is eventually consistent, and being wrong by a handful during a partition is a commercial rounding error. The check that is *never* made is at runtime against cameras already recording — the rule from М12 that anything cached from above may keep recording forever applies to the licence with no exception. Withholding a licence removes the right to grow, never the recording
- **Lifetimes, not revocation.** The certificate argument of М12 Lesson 7, again: revocation assumes reachability, so the licence has none. What it has is `not_after` plus grace, and the two commercial shapes that follow from it — a **subscription** (short validity, auto-renewed on the pull path, so a lapsed payment becomes *cannot add cameras* after grace) and a **perpetual** licence (no `not_after`; only update access is time-boxed). The second is the honest answer to *what if you are gone*, and a vendor that will not offer it is asking the customer to bet the estate on the vendor's survival
- **What the product reports upward**, at the customer's discretion, and it is the same channel as support inventory: domain id, licence serial, cameras in use, features in use. Enough for the vendor to bill and to spot a duplicate id; nothing else. The datasheet lists the fields
- **What it looks like from the console.** `entitlement_seconds_remaining` is a gauge (М13 Lesson 1's emitted signals), placement exports `cameras_licensed` and `cameras_in_use`, and the alarm is on the product — *thirty days to the floor* — not on a licence server being unreachable, which the product cannot even observe
- **Trial and feature gating** as the same mechanism with smaller numbers: a trial is a licence with a short window; a feature is a boolean in the document that a detector process reads at start. No second system

**Deliverable:** a vendor-side issuer (a CLI that signs a JSON document with an Ed25519 key, and a table it reads from), a domain that verifies it with the shipped public key and refuses the twenty-first camera; the licence expires, the grace runs out, and the twenty cameras keep recording while the console counts down; a renewal arrives through hawkBit and the count reopens; and the same document dropped into a second domain works — and shows up in the consumption view as two domains with one id.

---

### Lesson 4 — Publishing, and rollout across customers

- **A licence is a bundle with no payload.** It travels the path Lesson 3 chose — published by the vendor, pulled by the domain's update server, carried by hand when air-gapped — so the rollout machinery below already moves licences too
- **Publishing a bundle:** signing with the vendor's key (М9 Lesson 2's chain, from the other side), and pushing to each domain's update server — never to a box
- **Rollout as a population operation:** canary customers, rings, and **a halt condition that fires automatically** — М13 Lesson 3's alert rule, now pointed at a fleet
- **Version skew across the fleet is the normal state.** You cannot update every customer at once, so the controller–worker contract tolerates N−1 and preferably N−2 — which is where М12's opaque config and revision ordering pay off across the fleet, not just within a domain
- Schema migration across a skewed fleet: expand, migrate, contract — never a breaking change in one step, because some domain is always a version behind

**Deliverable:** a rollout to three simulated customer domains that halts itself when the canary's `camera_silent_seconds` rises — and a bundle that reaches an air-gapped domain by hand and installs identically.

---

### Lesson 5 — The hosting business, support, and what a vault is actually for

- **Rented capacity as a commercial offer.** A rented cluster is just a cluster (М12 L8). What changes when the vendor is the landlord: on-call for hardware the customer does not own, egress costs for video that is not yours, and a compliance surface. **The module frames this decision and does not take it**
- **Multi-tenancy, if you are hosting.** One vendor, many customers' cloud credentials and integration secrets — and this, finally, is what **OpenBao** is for: dynamic credentials and secrets held on behalf of many tenants. Everything else the course once assigned to a vault was removed by giving machines identities
- **Support inventory:** what a domain reports upward, at the customer's discretion; what the vendor's support view can and cannot answer; and the divergence idea from М12 Lesson 1 across customers — something running that no domain reported is a gap in the model, and two domains reporting one domain id is Lesson 3's copied licence, found the only way it can be
- **What the vendor must never be able to reach**, restated as a test: a support engineer with full vendor access attempts to read a customer's configuration, footage, or credentials, and the attempt fails by construction rather than by policy

**Deliverable:** the vendor-side view across three domains, and a written statement of every question it cannot answer — plus the test that a full-access vendor account cannot read a customer's camera password.

---

## Verification plan

**Track 1 — verified in the authoring sandbox.** Nearly all of it: a simulated MASA is an HTTP service signing vouchers; the licence system is a signing CLI, a small database and a signed document verified offline with `openssl pkeyutl`; the count is enforced by a placement service against fake clusters; rollout is a state machine against fake domains; the boundary document is prose. The vendor-disappears demo needs only the domain from М12 and a switch.

**Track 2 — needs real hardware.** An IDevID installed at manufacture, and a TPM — neither can be simulated in a way worth teaching. A student without them reads Lesson 2's first half and runs the approval fallback instead.

---

## Open questions

1. **Is the vendor in the hosting business?** The sentence *provide allocations in the cloud* decides it, and the module frames the choice without taking it.
2. **Does the vendor run a MASA at all?** Without one, BRSKI is unimplementable and approval is the only enrollment path — which many customers prefer anyway.
3. **How much inventory does a customer report?** The default should be minimal and the datasheet should say what it is; a vendor that asks for more has to say why.
4. ~~**Should observability come before this module?**~~ It does now. Resolved when the remote observer was recognised as a domain service.

---

## Sources

- [RFC 8995 — BRSKI](https://datatracker.ietf.org/doc/html/rfc8995) — pledge, registrar, MASA, voucher, IDevID and LDevID; the registrar belongs to the domain, the MASA to the manufacturer
- [Eclipse hawkBit](https://eclipse.dev/hawkbit/) — pull-based update delivery; the vendor publishes, the domain's server serves
- [М12's design](../М12_DomainVMS/module-design.md) — the domain as its own root, and the contracts that let a fleet run on mixed versions

*Written 5 September 2026 as FederatedVMS; became OrchestratedVMS on 7 September; became VendorVMS the same day, when the layer it described turned out not to exist. The licence system became its own lesson on 8 September.*
