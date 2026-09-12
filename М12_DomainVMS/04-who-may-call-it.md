# Lesson 4 — Who May Call It

**Module:** DomainVMS — the smallest layer above a set of clusters (Module 12)
**You will build:** a domain signer that issues tokens naming a subject and nothing else; Nodes that verify them offline against a public key and check their own grants; an agent that carries trust into every cluster; identity that never reaches a Node; a revocation window stated in advance and then measured; and break-glass, out loud.
**Time:** ~180 minutes.

## Why this lesson exists

Lesson 3 built a write API on every Node, and it is unauthenticated. That is N endpoints where there used to be one. Node-owned configuration is why an operator can edit a camera while the domain is unreachable — and it is also why the thing to protect is now per-Node. This is a real cost of the design and it belongs next to the benefit rather than three modules later.

The lesson is in two halves that turn out to be one argument. The first is *what* protects a Node's API: a channel, then a caller, then a local check that needs no network. The second is a defect the course has been carrying since М9 Lesson 9 — a login against a local `operators` table with a password hash — and what it becomes on N Nodes: N Alices, N stealable hashes, and an account that outlives the grants it was meant to bound. Both halves resolve the same way, and it is the move this course keeps making: **delegate an authority; do not distribute a secret.**

> **What you can verify without hardware.** Tokens, the key set, the revocation list, users, grants, the agent, break-glass and the identity restore all run in `tests/test_lesson4_identity_grants_agent.py`, with a clock. The revocation window is stated by `access_ends()` and then measured by moving that clock. The mTLS channel itself — certificates on the wire — is Lesson 7's `TrustBundle` and the bench.

## Prerequisites

- **Lesson 3** — the console's write API and the `verifier` hook it left empty.
- **М11 Lesson 2** — Variables and their ACL: one writer per prefix. The agent is that pattern with a new prefix.
- **М11 Lesson 3** — publish-then-point. The identity set travels the same way a Node's configuration does.
- **М9 Lesson 9** — the login marked temporary, and the `operators` and `grants` tables it left behind.

## Learning objectives

1. Order the protections: channel, caller, local check — and say which survive the domain being down.
2. Issue a token that names a subject and nothing else, and verify it offline against a key set.
3. Put users where they belong and prove nothing about them reaches a Node.
4. Carry trust into every cluster with an agent that can write `domain/*` and nothing else.
5. Enforce with Node-local grants that expire, and state then measure the revocation window.
6. Say why М9's `operators` table is superseded, not extended — and what break-glass costs.

---

## Step 1 — The channel, before the caller

Every stream in this module — configuration upward, grants downward, status both ways — runs **mTLS from the domain's own self-signed root**. A credential says who is calling; it says nothing about the channel. The root is hand-provisioned in the sense that a student runs `openssl` (or, here, `Signer.__init__`) to make it, and **it is not a stand-in**: this is the customer's root, permanently, and Lesson 7 gives it lifetimes and rotation. The certificate names the **Node**, never the server it happens to run on — failover relocates the Node, and a hostname-shaped name would have to be reissued on every move.

The per-Node credential that authenticates on that channel is hand-provisioned in *this* lesson and marked temporary, exactly as М9 hand-provisions AWS keys and М9 a database password. Lesson 6 replaces it with a certificate the box earns by enrolling.

## Step 2 — Delegate an authority, do not distribute a secret

The defect first. М9 Lesson 9 put a login on the console against a local `operators` table. On one box that was right. On N Nodes it means four accounts for one person, four passwords she will make identical, four hashes an attacker can take — and worse, **a grant expires and the account does not.** Revoke Alice's grants and her credential still authenticates on every Node; you have bounded the authorization window and left the authentication window unbounded.

The fix is the same one the CA made a paragraph ago:

```
Alice ──▶ the domain signer ──▶ a short-lived signed token (sub: alice)
                                        │
                                        ▼
                          Node 3: verify signature (public key, OFFLINE)
                                  check expiry, check the revocation list it holds
                                  look up ITS OWN grants for "alice"
```

`domain/tokens.py` is that token: the JWS shape — `base64url(header).base64url(payload).base64url(signature)` — with one algorithm (Ed25519) and no library, so a Node verifies it in forty lines and a public key:

```
token:   eyJhbGciOiAiRWREU0EiLCAia2lkIjogImI4MmRi...   (269 bytes)
payload: {'exp': 1757500900.0, 'iat': 1757500000.0, 'iss': 'acme', 'jti': '28ec559f5a84fc9e', 'sub': 'alice'}
```

Read what is not in the payload: no roles, no grants, no cameras. **The token names the subject and nothing else.** What Alice may do is each Node's own table (Step 5), because a token that carried rights would be a lookup that expired with the domain. `verify()` returns the payload or raises `Expired`, `Revoked`, `UnknownKey`, `BadSignature` — and takes a `KeySet`, not a key, so that rotation (Lesson 7) is an overlap and not an outage.

**N Nodes holding password hashes is N places to steal them from. N Nodes holding a public key is zero.** That is a security improvement, not a tidiness one. М9's `operators` table is superseded, not extended: a student who keeps it and adds a `node_id` column has built the N-Alices problem on purpose.

## Step 3 — Where users live, and what touches a Node

Creating a user touches no Node. `IdentityStore` writes one record under `identity/users/<id>` in the domain cluster's Variables — small, rare, consistent, beside the signer's key, under the same one-writer-per-prefix ACL (`deploy/signer-policy.hcl`). A local user holds a scrypt hash; where the customer has an IdP, the record holds an OIDC subject and no secret at all: Alice authenticates against her employer, the signer issues a *domain* token naming her, and the Nodes never learn the IdP exists. Per-user UI configuration — walls, layouts — is an object (`users/<id>/prefs`), last write wins with a revision so a stale tab is *told*.

Then the test that is the point of the step. After the domain agent has synced into the south cluster:

```
domain vars:                 ['domain/signer', 'identity/users/alice']
south vars after agent sync: ['domain/keys']
```

Nothing about Alice is in south. What arrived is the signer's public key set, and (when there is one) the revocation list. `DomainAgent` is one small Nomad job per cluster (`deploy/agent.nomad.hcl`) whose only right is to write `domain/*` in that cluster's Variables — the way a Node's only right is `nodes/<node>/*`. The test then makes the agent try to write `nodes/node-4/epoch` and gets `Forbidden`. Nodes read the key set from their **own** cluster's Variables (`NodeTrust`), never from the domain.

And the identity set is published the way a Node's configuration is: `IdentityStore.publish()` writes the whole set as one object, then moves the pointer — `identity/pointer → identity/rev-N` — on a floor. Losing the domain cluster loses users only back to the last publication, and `IdentityStore.restore()` on another cluster is М11's restore with different nouns: the backed-up signer key, then the object the pointer names. The RPO for users is the publication interval, and it is stated.

## Step 4 — The domain is down

```
domain down, agent.sync(): False | node still authorises: alice
new login: Unreachable
```

The agent stops updating and writes nothing. The Node keeps verifying with the keys it has — a signature check needs no network. Issued tokens run to their expiry. Nobody *new* logs in, because the signer is behind the link that is down. That is the bounded outage the services table promised, with the mechanism in front of you: the only thing the domain's absence removes is the issuing of new tokens, and Lesson 7's arithmetic — certificate and token lifetimes chosen from the outage you must survive — is what decides how long that is tolerable.

## Step 5 — Grants are Node-local, and expiry is the revocation mechanism

Each Node stores *subject X may do Y on camera Z until T*. Enforcement is a local query — no lookup, no token exchange — which is the only way authorization survives the domain being down. It also partitions privilege: a compromised Node can grant rights only on itself, where a central store compromised is total.

```python
class NodeGrants:
    def may(self, subject, capability, camera, now=None) -> bool: ...        # a dict lookup and a comparison
    def renew_from_domain(self, renewals): ...                               # the upward stream: replace, so a dropped grant is not renewed
    def access_ends(self, subject, token_exp) -> float: ...                  # state it in advance
```

Now the asymmetry that makes rights different from configuration:

| If the write does not reach the Node | Result | Visible? |
|---|---|---|
| A camera edit | records the old way | **Yes** — you can see it |
| A **grant** | the operator cannot get in | Yes — they complain |
| A **revoke** | **the removed administrator keeps the site** | **No** — and they have every incentive not to mention it |

Configuration staleness is benign and self-announcing. Revocation staleness is silent and adversarial, and its window is *unbounded* — until someone reaches that Node, which may be weeks. A grant carrying `valid_until`, renewed on the same upward stream that already carries configuration, converts that into **a number the product states**: a Node that cannot renew lets its grants lapse.

Two lifetimes, and they are not independent:

| | Too short | Too long |
|---|---|---|
| **Token** (`TOKEN_LIFETIME`, 15 min) | Alice is logged out mid-incident and cannot re-authenticate if the domain is unreachable | a revoked employee keeps working until it expires |
| **Grant** (`GRANT_LIFETIME`, 24 h) | a site in a long outage locks out its own operator | a revoked administrator keeps the site |

A token outliving its grant is harmless — the Node finds no grants and refuses. A grant outliving every token is harmless — nobody can present a subject. The failure is assuming one covers the other. The revocation window is **the shorter of the two**, and most people answer the token:

```
access_ends (revoke cannot reach node-4): 900.0 s      window: 900.0
```

The test states that number with `access_ends()` before touching the clock, then advances the clock past it and shows `authorise()` refusing — *expired* — with the grant still in the table. Then the other direction: a fresh token after the grant lifetime, refused — *no view grant* — until the upward stream renews what the domain still grants and drops what it does not.

## Step 6 — Wire it into the console

Lesson 3 left `ConsoleAPI(verifier=None)`. The verifier is three lines:

```python
def verifier(token) -> str:
    ks = NodeTrust(cluster.vars).keyset()          # from THIS cluster's Variables, put there by the agent
    return verify(token, ks, trust.revoked())["sub"]
```

and the console's responses stop saying `"authenticated": false`. The console verifies the *token*; the **Node** decides the *grant* when the forwarded edit arrives, because the console cannot survive the domain being down either and must not be where enforcement lives. The live gateway (Lesson 3) does the same: relays, never authorises.

## Step 7 — The honest residue: break-glass

Alice is on site, the uplink is down, and her token expired an hour ago. No amount of design removes that case. A local emergency account is what real products ship, and it reintroduces exactly the password hash this lesson removed. The defensible version is `BreakGlass`: **one** account, audited on every use (success *and* attempt), alarmed on, and rotated after — and a module that says this out loud rather than pretending the clean design has no edge.

```
BREAK-GLASS used by carol: uplink down, token expired
audit: [{'at': ..., 'who': 'carol', 'why': ..., 'ok': False}, {'at': ..., 'who': 'carol', 'why': ..., 'ok': True}]
```

The token it issues carries `via: break-glass` and `who: carol`, so a Node's grant check can treat the subject `break-glass` differently and the events say who was holding it.

**Deliverable:** grant an operator rights on a Node, then revoke them while that Node is unreachable — and state, in advance (`access_ends()`) and then by measurement (the clock), exactly when their access ends. Then delete М9's `operators` rows on every Node and show that Alice still logs in.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `UnknownKey` on every Node in one cluster | The agent there has not synced, or its token lacks `domain/keys` write. `nomad var get domain/keys` in that region. |
| `UnknownKey` after a key rotation, on old tokens only | The previous key's overlap has ended (`retire:` in the key set). Expected after the overlap; a token older than the overlap was already past its own expiry. |
| Alice can log in but every Node refuses her | She has a token and no grants. Grants are written to each Node through its console; a new user has none anywhere. Correct. |
| A revoked administrator still has access on one Node | That Node has not renewed its grants — the upward stream is down. Their access ends at `access_ends()`; if that number is a week, the number is the bug, not the Node. |
| `Expired` immediately after issue | Clock skew between signer and Node beyond `verify()`'s 60 s tolerance. Lesson 7 names this; NTP fixes it. |
| The identity restore refuses | The pointer names an object the backup store does not hold — publication order broken, or the backup did not copy the latest object. Refuse to guess; restore the previous revision explicitly. |

## Recap

- Channel (mTLS from the domain's root), then caller (a token), then a **local** check (grants) — and only the last two need to survive the domain being down, and both do.
- The token names the subject and nothing else. Nodes hold a public key set, never a hash.
- Users live in `identity/*` in the domain cluster's raft, published object-first; nothing about them reaches a Node. The agent carries the key set and the revocation list into `domain/*` of every cluster and can write nothing else.
- Grants are Node-local with `valid_until`; expiry is the revocation mechanism; the window is the shorter of the two lifetimes, stated, then measured.
- М9's `operators` table is superseded. Break-glass is one account, audited, alarmed, rotated — and admitted.

## Exercises

1. Set `TOKEN_LIFETIME` to four hours and `GRANT_LIFETIME` to fifteen minutes. Re-run the window test, state the number, and say which operator you have just locked out during a long outage.
2. Put roles into the token and remove `NodeGrants`. Then make the domain unreachable and revoke Alice. When does her access end?
3. Give the agent write on `nodes/*` "for convenience" and describe the first thing a compromised agent does.
4. The IdP is down but the domain is up. Who can log in? Now the reverse. Write both answers as one sentence each for the datasheet.
5. Design the alarm for break-glass: where it goes, who acknowledges it, and what "rotated after" means when the person who used it is the one who would rotate it.

## Where this is going

The Node still authenticates on the channel with the credential someone typed in Step 1. [**Lesson 5**](05-packaging-updates-and-the-licence.md) first settles how the domain ships and updates itself and what a licence does at this level; then [**Lesson 6**](06-secure-introduction-a-box-joins-the-domain.md) replaces that typed credential with one the box earns.
