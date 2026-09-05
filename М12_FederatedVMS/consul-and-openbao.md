# Consul and OpenBao — Two Answers to mTLS

**A decision record for М12_FederatedVMS.** Companion to [`kubernetes-vs-nomad.md`](../М9_EdgeVMS/kubernetes-vs-nomad.md), [`rauc-alternatives.md`](../М9_EdgeVMS/rauc-alternatives.md) and [`apphost-and-process-model.md`](../М9_EdgeVMS/apphost-and-process-model.md), written in answer to "Consul or OpenBao?" — which turns out to be the wrong question, in a useful way.

---

## Verdict

**They are not alternatives.** Consul is a layer-2 component — service discovery and a service mesh. OpenBao is layer 5 — secrets and PKI. They overlap on exactly one thing: **where mTLS between services comes from.**

**No Consul**, and not for the reason you would expect. Consul's certificate authority is not worse than the one М12 designs. It is, on inspection, *the same design* — which is the most interesting thing in this document. The decision turns on something else: the product needs a PKI that a service mesh structurally cannot provide, so it is running one regardless, and a second certificate hierarchy is a cost with no matching benefit.

---

## What each one actually is

| | Consul | OpenBao |
|---|---|---|
| Layer | 2 — workload | 5 — secrets and trust |
| Primary job | Service discovery; optionally a service mesh | Secrets storage, dynamic credentials, PKI |
| Issues certificates for | Mesh services and client agents | Anything you define — devices, services, people |
| Licence | **BUSL**, Licensor IBM | MPL-2.0, Linux Foundation |
| Data plane | An Envoy sidecar per service | None — it hands out credentials |

---

## The overlap, and a surprise

Consul's mesh CA and the PKI М12 designs are the same structure, arrived at independently:

| | Consul's mesh CA | М12's PKI |
|---|---|---|
| Root | Primary datacentre | Offline root |
| Per-locality intermediate | Secondary datacentres get an intermediate CSR signed by the primary root | One intermediate per domain, signed by the root |
| Intermediate lifetime | 8,760 hours — one year | ~1 year |
| Leaf lifetime | 72 hours by default | Hours to days |
| Why | So a secondary keeps working when the primary is unreachable | So a site keeps working when the centre is unreachable |

That convergence is worth taking seriously: it is independent confirmation that **root → per-locality intermediate → short leaf** is the right shape for anything that has to keep issuing certificates while partitioned. М12 should say so rather than presenting its design as novel.

**The difference is not the shape. It is what the CA issues certificates *for*.**

Consul's leaf certificates serve mesh proxies and its own client agents. They exist for services that have already joined. Nothing in a service mesh answers the question М12 is actually built around — *how does a box that has never been on this network, holding no secret, obtain an identity?* No mesh issues an LDevID to a pledge. There is no BRSKI in a service mesh, because that is not what a service mesh is for.

So the PKI is not optional. It is the module.

---

## Why one hierarchy and not two

1. **Device identity is out of a mesh's scope.** You are running a PKI for enrollment, device certificates and offline renewal whether or not Consul is present.

2. **HashiCorp's own recommended configuration points the same way.** Consul's CA supports a Vault provider precisely so root keys stay in the vault and are never exposed to Consul servers. In the mature configuration these are not competitors — the mesh CA becomes a *consumer* of the vault. If you are running OpenBao anyway, adding Consul does not remove a dependency; it adds one on top.

3. **Two hierarchies are two of everything.** Two rotation procedures, two sets of expiry alarms, two chains to reason about at 3am, and two ways for a certificate problem to look like a network problem.

4. **The sidecar has a measurable cost.** A mesh means an Envoy proxy per service. М10 taught students to measure a worker's memory footprint precisely because appliance budgets are finite; adding a proxy per service to a box already running twenty media workers is a number, and it should be measured rather than waved away.

5. **It is a second BUSL dependency.** [`kubernetes-vs-nomad.md`](../М9_EdgeVMS/kubernetes-vs-nomad.md) works through the licence text: Nomad and Consul are both BUSL with IBM as licensor, in a product that is *shipped to customers on hardware* — the word the Additional Use Grant uses is "embedded". Dropping Consul leaves **Nomad as the only unavoidable BUSL component**, which is a materially easier answer to give an acquirer than three.

---

## What dropping Consul actually costs

Stated plainly, because this document is worth nothing if it only lists the wins.

| Given up | Replaced by | Honest? |
|---|---|---|
| DNS-based service discovery | Nomad's native service discovery — templated service addresses | Weaker. No dynamic DNS |
| Health-check-filtered discovery | Nothing equivalent | **A real loss.** Nomad's native discovery does not filter by HTTP/TCP/gRPC health |
| Service mesh with L7 policy | mTLS from the PKI, authorisation at the API boundary | Fine at a handful of services; not at fifty |
| Mesh gateways across datacentres | Nothing — cross-site traffic is the federated API, not a mesh | Acceptable, because that traffic is deliberately narrow |
| A distributed KV store | Postgres, which М10 already put on the box | An improvement, not a loss |

The health-check gap is the one to watch. At edge scale — a handful of services per site, most of them supervised by systemd or Nomad already — it does not bite. It would bite in a datacentre.

---

## When Consul earns its place

Not never. Specifically:

- **Many services, not a handful.** The operational overhead is roughly fixed; the benefit scales with service count
- **L7 authorisation that certificates cannot express** — policy on paths and methods, not just "may service A talk to service B"
- **A genuinely heterogeneous mesh.** Consul's cross-platform reach across Kubernetes, Nomad, VMs and bare metal is real, and if the deployment looks like that, it is doing work nothing else does
- **A datacentre, not an appliance**, where sidecar overhead is noise and the primary is not on the far side of a customer's flaky uplink

None of those describe a VMS appliance at one site.

---

## What this means

| Criterion | Favours |
|---|---|
| Device identity and enrollment | **OpenBao** — a mesh does not do this at all |
| Certificate issuance while partitioned | **Either** — the designs are equivalent |
| Fewest moving parts on an appliance | **OpenBao** — no sidecars, one hierarchy |
| Licence cleanliness for a shipped product | **OpenBao** — MPL vs a second BUSL component |
| Health-based service discovery | **Consul** — the one genuine loss |
| Many services with L7 policy | **Consul** — but that is not this deployment |

**Course decision: teach OpenBao, name Consul, and teach the comparison.** Students shipping commercial products need to be able to tell a component they need from a component that is merely adjacent to one they need — and "Consul or OpenBao" is a good example of a question whose framing is the error.

**Product recommendation: OpenBao's PKI, Nomad's native service discovery, no Consul.** Revisit if the service count grows past what a person can hold in their head, or if L7 policy becomes a requirement. Record the health-check gap as a known, accepted cost rather than discovering it later as a surprise.

---

## Sources

- [Consul service mesh certificate authority](https://developer.hashicorp.com/consul/docs/connect/ca) — CA providers, secondary-datacentre intermediates signed by the primary root, 72-hour leaf and one-year intermediate defaults, and what leaf certificates are issued for
- [Consul service mesh architecture](https://developer.hashicorp.com/consul/docs/architecture/data-plane/connect) — the sidecar data plane
- [Cross-datacentre mesh gateways](https://developer.hashicorp.com/consul/docs/east-west/mesh-gateway/federation) — what federation in a mesh means, and how it differs from this course's use of the word
- [RFC 8995 — BRSKI](https://datatracker.ietf.org/doc/html/rfc8995) — the device-onboarding problem no service mesh addresses
- [`kubernetes-vs-nomad.md`](../М9_EdgeVMS/kubernetes-vs-nomad.md) — the BUSL licence reading, applying equally to Consul
- Nomad native service discovery and its limits, as recorded in [`COURSE-PLAN.md`](../COURSE-PLAN.md)

*Checked against current documentation, 5 September 2026.*
