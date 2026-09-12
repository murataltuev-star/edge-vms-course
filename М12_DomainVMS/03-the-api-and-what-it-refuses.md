# Lesson 3 — The API, and What It Refuses

**Module:** DomainVMS — the smallest layer above a set of clusters (Module 12)
**You will build:** the camera list every UI wants, assembled from what Nodes already publish; a write API that forwards to the owner and refuses to set placement; and the two processes that serve browsers so that a Node never has to.
**Time:** ~180 minutes.

## Why this lesson exists

Every screen an operator opens starts with the same list: every camera, its name, its site, whether it is recording, when it was last seen — across Nodes and across clusters. The architecture so far cannot draw it. The directory answers *where* camera 7 is, and holds camera ids and a pointer to a blob; the name, the phase and `last_seen` live in each Node's Postgres, because М9 put them there and М11's *a Node owns its configuration* keeps them there. So the list exists nowhere and has to be assembled, and the wrong way to assemble it is the obvious one.

The second half of the lesson is a question the Node-centred modules never had to ask: who talks to people? М9 put a console on the Node and it was the right console for the right client — the Node's own status, one query. It never said who is allowed to be that console's client, and the answer decides whether every browser tab is a subtraction from the camera count.

> **What you can verify without hardware.** The read model, the causes, the API façade and the gateway's contract run against fakes in `tests/test_lesson3_readview_api_gateway.py`, including the console over real HTTP on a random port. Two hundred cameras across four Nodes, a server killed, one cause — that is a test. WebRTC, fMP4 and TURN are the transport under the gateway's contract and need a browser and the bench.

## Prerequisites

- **Lesson 1** — the directory of directories. Writes go through it to find the owner.
- **М11 Lesson 4** — the heartbeat as an object, and why it left raft. This lesson is that object carrying its payload.
- **М11 Lesson 3** — `replicated`. It stays the only place the UI learns an edit reached the cluster.
- **М9 Lesson 9** — positions and reasons; the Node's console; the login marked temporary.
- **М9 Lesson 7** — `B + n·I`. The reason a Node must not serve browsers is that formula.

## Learning objectives

1. Assemble the camera list from published snapshots and say why not from a fan-out and not from Variables.
2. Show staleness on every row and never present a Node's silence as its cameras' absence.
3. Group silence by failure domain so a dead server reads as one cause.
4. Forward writes to the owning Node with idempotency keys, and refuse what a client may not set.
5. Split "serving browsers" into a console and a live gateway, and give the failure arithmetic of each.
6. Keep enforcement at the Node when a gateway relays a viewer's token.

---

## Step 1 — Three ways to assemble a list, and the shape rule

М11 Lesson 2's rule — small, rare and consistent is raft; large, rare and never queried is an object; everything a Node needs at once is its Postgres — decides this before anything is built:

| | What it is | Why not |
|---|---|---|
| **Fan-out** | the console discovers every Node and calls N consoles per page | every page waits for the slowest Node; the first dead Node hangs the list or forces partial-response logic into every screen; each refresh is N calls. Works at three Nodes, fails at thirty |
| **Status in Variables** | every Node adds its camera phases to its own Variable | two hundred cameras from fifty Nodes every ten seconds is a hundred raft commits a second replicated to every server, for data nobody looks up by key — precisely why the heartbeat left raft |
| **Published snapshots** | every Node's heartbeat object carries its own `/status`; the console reads N small objects and holds them in memory | frequent, medium, never queried by key: **an object**. No Node is called. No raft is written. A dead Node costs a stale snapshot |

The third is the decision, and it is not a new mechanism. The Node already has a heartbeat task; it grows from `{ts, epoch}` to

```json
{"ts": 1757500000.0, "epoch": 3, "revision": 12, "server": "srv-1",
 "cameras": [{"id": 7, "name": "gate", "site": "hq", "enabled": true, "phase": "running",
              "revision": 12, "observed_revision": 12}, ...]}
```

That is `ClusterAppHost.heartbeat_payload()` in М11's `clustervms/` (and `HeartbeatPayload()` in the Go port) — the one change this module made downward. The arithmetic is why it is cheap: two hundred cameras at roughly two hundred bytes each is a 40 kB object per Node every ten seconds; fifty Nodes are 200 kB/s into an object store sized for footage restore points.

## Step 2 — The read model

`ReadView.refresh()` is one pass: for each cluster, scan its directory for Node names, `get` each Node's heartbeat object, keep it. `rows()` flattens what it holds into camera rows, each carrying the age of the snapshot it came from. Nothing else touches a Node.

```
{"total": 200, "page": 1, "size": 2, "clusters": {"north": "ok", "south": "ok"}, "complete": true}
{"camera": 1, "name": "cam1", "site": "hq", "node": "node-1", "cluster": "north", "server": "srv-1",
 "phase": "running", "conditions": {}, "revision": 1, "epoch": 1, "age": 3.0,
 "node_state": "live", "as_of": "as of 3 s ago"}
```

Three properties, and each is a sentence in the design record. **It is not a database** — it holds nothing it cannot rebuild from the objects in one pass, and a restart of the console *is* that pass. **Staleness is shown, never hidden** — every row prints its age; a Node older than `lost_after` (45 s, М11 Lesson 4's `lost_after`) becomes *stale — last known state, 103 s old* with its cameras still listed, greyed. **A cluster that did not answer is reported as such**, its rows kept from the last successful pass — never rendered as an empty cluster, which is Lesson 1's *not mine* versus *not anywhere* applied to a screen:

```
clusters: {"north": "ok", "south": "unreachable"}   complete: false
```

This is М9's *desired is persisted, actual is derived* one layer up. The snapshots are actual state; a copy of actual state is only ever a cache, and this one admits it.

## Step 3 — One cause

The heartbeat carries `server` — the Nomad client the Node runs on. So when a server dies, its Nodes go silent *together*, and the console can say so once instead of greying a hundred cameras:

```
['server silent: north/srv-1 for 103 s — 2 Node(s), 100 camera(s)']
```

`causes()` groups silence by the largest failure domain that explains it: a whole cluster unreachable is one cause; every Node on a server silent is one cause; a single Node silent among live ones on the same server is its own. This is the *grouped by failure domain* line from the design record, and it is what turns the deliverable's "kill a server" into one line on a screen. It is also the difference between an operator who reads *srv-1 is down* and one who reads a hundred camera alarms and starts with the first.

## Step 4 — The write API, and what it refuses

The console owns nothing, so its write API is a façade:

```python
def update_camera(self, camera, fields, idempotency_key, token=None):
    if idempotency_key in self._seen:
        return self._seen[idempotency_key]            # the same PUT, not a second edit
    self._refuse_placement(fields)                     # node, cluster, placement, epoch, observed_revision, phase
    subject = self._subject(token)                     # None until Lesson 4
    ans = self.directory.where(camera)
    if not ans.found:
        raise ApiError(404 if ans.complete else 503, ans.sentence())
    result = self.consoles(ans.node).update_camera(camera, fields, subject)
```

Read the refusals. A client may not set `node` or `cluster` — placement is decided and stored by the placement service with a reason, never dictated by an edit. It may not set `phase` or `observed_revision` — those are controller-owned, and М9 Lesson 5 said so. And a camera the directory cannot find gets a `404` only if the answer was complete; if a cluster was unreachable the honest code is `503`, because *not found* and *could not look* are different failures and a client that retries on one should not on the other.

The idempotency key is what makes a PUT safe to retry over a link that drops: the retried request returns the first response and the owning Node sees one edit. The edit reaches the Node through *its* console, so the owner does not change, one-writer-per-key is untouched, and `replicated` (М11 Lesson 3) remains the only place the UI learns the edit reached the restore point.

The API is unauthenticated in this lesson, and every response says so: `"authenticated": false`. Lesson 4 is where the `verifier` arrives.

## Step 5 — Who serves browsers

Everything so far is recorders talking to stores. Now people.

**A Node serves few, trusted, internal clients. Something else serves many, untrusted, external ones.** A Node's memory is `B + n·I`, budgeted for cameras; a browser is numerous, on a bad network, behind NAT, inclined to open six tabs and leave them. The moment a Node serves browsers, a slow viewer on a Saturday night competes with recording for the same process. So the Node's clients are exactly two, and they are separate processes because they fail differently:

**The console** — the UI's static files, the API above, the read model, TLS, token verification. Stateless, `count = 2`, placed anywhere (`deploy/console.nomad.hcl`). `python3 -m domain.console` is the standard-library version; the test drives it over HTTP:

```
GET  /api/cameras?q=&page=&size=&cluster=     PUT /api/cameras/7   (Idempotency-Key required)
GET  /api/causes                              GET /api/where/7
```

**The live gateway** — turns a Node's one live stream into fifty browser sessions: WebRTC (WHEP) for live, fMP4 over HTTP for playback, TURN when browsers are behind NAT, transcoding where a browser cannot decode what the camera sends. It is the middle tier М9's process-model note predicted — *demand-driven, sized by concurrent viewers, hardware-bound* — and it is the one legitimate place a specific server comes back: a gateway that transcodes wants the GPU, one that faces the internet wants the public address, and both are **constraints** in `deploy/gateway.nomad.hcl`. Nomad places it on that server because of them; nobody types its name.

## Step 6 — The tee, and one subscription

Where does the gateway's picture come from? Two sources, and the cameras decide. Most IP cameras serve several RTSP sessions, so the gateway may open the camera's *sub-stream* directly while the Node records the main profile. Where the camera cannot — session caps, a saturated uplink, a DriverPack source with no second session — the Node's media worker carries a `tee` after the parser: one branch into `splitmuxsink` as always, one into a local live endpoint.

`domain/gateway.py` is the contract of that endpoint, without the transport:

```python
class LeakyQueue:          # bounded; a full queue drops its OLDEST frame; push() never blocks
class LiveTee:             # the Node side: subscribers are gateways, never browsers; push() is fire-and-forget
class Gateway:             # ONE subscription per camera upstream; N viewer queues out; relays the token
```

The test puts fifty viewers on one camera and asserts what the design record promises: the tee has **one** subscriber, the gateway has fifty; a hundred frames pushed into a thirty-frame upstream queue leak seventy and the recorder's `push()` never waited; every viewer with a five-frame queue leaked its own twenty-five and nobody else noticed. A stalled browser costs itself frames. It cannot cost the Node anything.

The line to hold: **the gateway relays and does not authorise.** `Gateway.watch()` hands the viewer's token to `NodeLiveEndpoint.open()`, and the Node's own `authorise(token, camera)` — signature, then its grants (Lesson 4) — decides. A bad token is refused by the Node, not the gateway, because a gateway that authorised would be enforcement in a process that cannot survive the domain being down. And when a camera fails over, `reconnect()` asks the directory again and follows the endpoint; the viewers' queues survive the move.

## Step 7 — The failure arithmetic

| Down | What stops | What continues |
|---|---|---|
| **Console** | nobody logs in or sees the list | recording; live sessions already established |
| **Gateway** | live view and playback | recording |
| **Node** | its cameras go dark on the wall; the console shows them from the last snapshot, with the age | every other camera |

In no case does a web problem reach a recorder, and in no case does a recorder's process host a viewer. Both jobs run in **every cluster** — a single-cluster customer gets a screen and a picture with no domain at all — and the domain cluster's console is the same code with every cluster's stores behind it, which is the read model's *directory of directories* again, now with a picture under each row.

**Deliverable:** the console showing two hundred cameras across four Nodes; kill a server; **one cause displayed.** And a browser watching one of them live through the gateway, with the Node's own viewer count still one — the gateway — and the browser count on the gateway.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| The list is empty for a Node that is recording | The Node's heartbeat has not been written since it restarted (it needs a lease first, М11 Lesson 4), or the console's `lost_after` is shorter than `HEARTBEAT_INTERVAL`. Never the answer *the console should call the Node*. |
| Every Node on one server goes stale at once and there is no *server* cause | Their heartbeats disagree on `server` — `NOMAD_NODE_ID` unset, so each fell back to a hostname that differs per container. Set it in the jobspec's `env`. |
| A PUT is applied twice | The client generated a new `Idempotency-Key` on retry. The key belongs to the *intent*, generated once before the first attempt. |
| `503` on an edit for a camera you can see in the list | The list is from the last snapshot; the directory read just now found the cluster unreachable. Correct — the list said `complete: false`. |
| The Node's tee shows two subscribers | Two gateways, or one gateway that lost its record of the subscription and re-opened. The first is fine. The second is a gateway restart without `leave()`; the tee should time out subscribers nobody drains. |
| Frames drop for every viewer, not just the slow one | The *upstream* queue is leaking — the gateway's pump is slower than the camera. That is the gateway's CPU, not the Node's, and the reason it has a GPU constraint. |

## Recap

- The camera list is assembled from the snapshot every Node already publishes in its heartbeat — never a fan-out, never Variables.
- The read model is a cache that admits it: memory, rebuilt in one pass, age on every row, an unreachable cluster kept and named.
- `server` in the heartbeat is what lets a dead server read as **one cause**.
- The write API forwards to the owner with idempotency keys, refuses placement and controller-owned fields, and says `503` when it could not look.
- **A Node never serves a browser.** The console and the live gateway are cluster-level jobs, separate because they fail differently, placed by constraint.
- One subscription per camera on the Node's leaky tee; N viewers on the gateway; the token relayed, the Node deciding.

## Exercises

1. Set `lost_after` below `HEARTBEAT_INTERVAL` and describe what the operator sees. Then say why the number must be М11's `lost_after` and not a console setting.
2. Add the `conditions` from the Node's `/status` to the heartbeat (the payload leaves them out) and cost it: bytes per Node per interval at two hundred cameras with three conditions each.
3. Write the `causes()` case for a whole datacenter — every server in one cluster silent while the cluster's Variables still answer (the servers are up, the cameras' network is gone). Which scope is that?
4. The idempotency cache is in the console's memory and `count = 2`. Say what a retry that lands on the other instance does, and whether the Node's own revision check (М9 Lesson 5) saves you.
5. Replace the leaky queue with a blocking one on the *upstream* subscription and run the fan-out test. Then say, in one sentence, which process you just made a dependency of recording.

## Where this is going

The console has an API on every Node, and it is unauthenticated. That is N endpoints where there used to be one, and [**Lesson 4**](04-who-may-call-it.md) says what protects them: a token signed by the domain, verified offline; grants that live on the Node and expire; a revocation window the product states and then measures; and the honest residue, break-glass.
