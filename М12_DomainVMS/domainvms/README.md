# domainvms — М12, whole

The smallest layer that can sit above a set of clusters, be switched off, and be the top of the product — as code. Built **on** М11's `clustervms/` (imported, not copied) and through it on М9's `nodevms/`: the domain reads each cluster's Variables through М11's `Directory`, its object store through М11's adapters, and never holds a database.

```
domainvms/
  domain/
    federation.py     Lesson 1  N clusters, one directory of directories; an Answer that says what it could not reach
    placement.py      Lesson 1  which CLUSTER gets a camera, by reachability; stored with a reason, by CAS; a dead cluster is not a trigger
    shadow.py         Lesson 2  the divergence report: lagging / stalled / orphaned / unmanaged / conflict / stale_epoch; the exit criterion
    readview.py       Lesson 3  the camera list from the snapshots Nodes publish; age on every row; one cause per dead server
    api.py            Lesson 3  the write façade: idempotency keys; refuses placement; 503 not 404 when a cluster is unreachable
    gateway.py        Lesson 3  the live tee with a leaky queue; the gateway that subscribes once and fans out; the Node's viewer count stays 1
    console.py        Lesson 3  the cluster console over HTTP, standard library; `python3 -m domain.console`
    tokens.py         Lesson 4  Ed25519 tokens naming a subject and nothing else; a key SET for overlap; a self-pruning revocation list
    identity.py       Lesson 4  users in identity/* (the signer the only writer); IdP subjects with no secret; publish-then-point; prefs as objects; break-glass
    grants.py         Lesson 4  Node-local grants with expiry; NodeAuthoriser: signature, then its own table, never a network call
    agent.py          Lesson 4  the domain agent: keys and revocations into each cluster's domain/* and nothing else; `python3 -m domain.agent`
    signer.py         Lessons 4, 7  the domain signer: root, service and LDevID lifetimes, renewal with overlap, root rotation with a trust bundle and a cross-cert, clock skew named
    entitlement.py    Lesson 5  the licence cached in the domain cluster, verified against the product's vendor key, graceful for a stated period; recording never stops
    enroll.py         Lesson 6  pledge, registrar, a simulated manufacturer CA and MASA; the voucher path and the approval queue with audit and expiry
    cloud.py          Lesson 8  the bandwidth and cost arithmetic; a Node rendered three ways and diffed
    runtime.py, signer_service.py   wiring for the real processes (NomadVariables, the object store, HTTP)
  deploy/
    signer.nomad.hcl  console.nomad.hcl  gateway.nomad.hcl  agent.nomad.hcl   the four jobs; constraints, never hostnames
    signer-policy.hcl  agent-policy.hcl                                        one writer per prefix
    federation.hcl                                                             two regions, one gossip pool
    verify-bench.sh                                                            what needs a real bench, scripted
  tests/              37 tests, no Nomad, no Postgres, no browser — milliseconds
```

```bash
python3 tests/run.py                 # 37 tests
python3 -m domain.console            # CLUSTERS=north=http://nomad:4646|http://minio:9000/restore,...
```

## What each lesson's deliverable became

| Lesson | Deliverable in the design record | Where it runs |
|---|---|---|
| 1 | three clusters, one directory; find a camera in each; make one unreachable and show the console saying **what it does not know** | `test_lesson1_directory_and_placement.py`: `Answer.sentence()` says *not found in the 1 cluster(s) I could reach; south unreachable — not 'not anywhere'* |
| 1 | placement by reachability, stored with a reason; two placers are safe by CAS; a dead cluster is not a trigger | same file: two threads placing forty cameras with opposite preferences agree on every one |
| 2 | a divergence report and a written exit criterion | `test_lesson2_shadow.py`: the six kinds, slow versus stuck by distance and time, `exit_criterion()` |
| 3 | two hundred cameras across four Nodes; kill a server; **one cause displayed**; a browser watching live with the Node's viewer count still zero | `test_lesson3_readview_api_gateway.py`: 200 rows from four heartbeat objects, no Node called; `srv-1` dies and `causes()` returns exactly one *server silent* covering both its Nodes; fifty viewers, one subscription on the tee |
| 4 | grant an operator rights, revoke while the Node is unreachable, **state in advance and then measure** when access ends | `test_lesson4_identity_grants_agent.py`: `access_ends()` states the number, the clock proves it; both directions (token outlives grant, grant outlives token) |
| 5 | what degrades when the licence server is unreachable for a month, and what does not | `test_lesson5_entitlement.py`: valid → grace → degraded; `recording_allowed()` has no code path that returns False |
| 6 | a box enrolls from cold with nobody typing a secret, receives an LDevID, the hand-provisioned credential is deleted, nothing stops | `test_lesson6_enrollment.py`: the voucher path and the approval path, a stranger's IDevID and a wrong-domain voucher refused, unapproved requests expiring |
| 7 | a thirty-day outage; rotate the root under load; revoke a device on a stated schedule | `test_lesson7_lifetimes.py`: service certs dark after two days, LDevIDs fine; both leaves valid across the overlap, the old root retired on its date, a peer with only the old root served by the cross-cert; skew named |
| 8 | two clusters, one rented; a written bandwidth-and-cost estimate | `test_lesson8_cloud.py`: 50 × 4 Mbit/s = 200 Mbit/s and 2.16 TB/day → *mixed*; the Node's jobspec rendered for a rack, a rented instance and a split site is byte-identical from `group` down |

## What the design record says, as code

**No database.** `grep -r "postgres\|sqlite\|CREATE TABLE" domain/` finds nothing. Users are Variables under `identity/*`; the read model is memory rebuilt from objects; placement is Variables; the signer's keys are one Variable. `IdentityStore.restore()` and `Signer.restore()` are the re-hosting: the backed-up key, then the identity object the pointer names.

**The Node never learns a user exists.** `test_nothing_about_a_user_reaches_a_node_only_trust_does` lists the south cluster's Variables after the agent has synced: `["domain/keys"]`. Then it tries to make the agent write `nodes/node-4/epoch` and gets `Forbidden`.

**The token names the subject and nothing else.** `verify()` returns `{"sub": "alice", ...}` and the test asserts `"roles" not in payload`. What alice may do is `NodeGrants` on each Node, with `valid_until`, renewed from the upward stream and dropped when the domain drops them.

**Correctness from CAS, never from instance count.** `ClusterPlacer._store` re-reads on `Conflict` and returns whatever the other placer wrote; the race test runs two placers with opposite preferences.

**The heartbeat carries its payload.** This module changed one thing in М11: `ClusterAppHost.heartbeat_payload()` (and the Go port's `HeartbeatPayload()`) now write `{ts, epoch, revision, server, cameras: [...]}`. `ReadView.refresh()` reads that object for every Node the directory lists — the only thing the console ever reads from a cluster's object store.

## Verified where

Everything in `tests/` ran in the authoring sandbox and on the author's machine (Python 3.10/3.11, `cryptography` for Ed25519 and X.509). The stdlib HTTP console is exercised by `test_console_over_http`. What needs a bench is in `deploy/verify-bench.sh`: federated regions, the forwarded read, the agent's ACL against real Nomad, the signer's placement, and the console with a whole cluster's uplink pulled. WebRTC, fMP4 and TURN are the transport under `gateway.py`'s contract and are not here. A TPM is read about, not run.
