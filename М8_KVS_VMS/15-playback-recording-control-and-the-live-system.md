# Lesson 15 — Playback, Recording Control, and the Live System

**Module:** The Frontend & Assembly (Module 7)
**You will build:** the rest of `web/app.js` — click-to-seek, hls.js playback, the moving playhead, and the Start/Stop control — and then run the entire system end to end against the spec's own acceptance criteria.
**Time:** ~90–110 minutes.

## Why this lesson exists

Lesson 14 built a timeline that shows you what exists. This one makes it a tool: click a moment and watch it. That turns out to be four small problems, each of which has a wrong answer that looks right —

- converting a click into a *time range* the backend will accept (not just a timestamp),
- handing that range's URL to a video element that can't play HLS natively in most browsers,
- keeping a playhead honest about where playback actually is,
- and letting a button control a process it doesn't own, without ever lying about its state.

And then the part no individual lesson has done yet: running all of it at once and checking it against criteria written before any of it existed.

## Prerequisites

- Lesson 14 — the timeline renders and slides.
- Lesson 13 — the assembled server, with `/api/hls` and the three `/api/recording` routes answering.
- To see real footage play, you need footage: either the `kvssink` capstone from Lesson 10, or a stream you've published to some other way. Everything up to Step 6 can be built and verified against fixtures; Step 7 onwards needs the real thing.

## Learning objectives

1. Convert a click position into a validated playback chunk, clamped to its run.
2. Explain why clicking a gap needs no code to be ignored.
3. Load an HLS stream with hls.js, and fall back to native playback where it exists.
4. Track the playhead against real playback position, not against wall-clock time.
5. Present failures as instructions to the viewer while logging the real cause to the console.
6. Drive a Start/Stop button entirely from server responses, including the `409` and external-agent cases.
7. Run the whole system and check it against the spec's thirteen acceptance criteria.

---

## Step 1 — From a click to a chunk

A click gives you an x-coordinate. The backend wants a `start` and an `end`, and Lesson 12 taught it to reject anything outside `0 < duration <= PLAYBACK_CHUNK_SECONDS`. Getting from one to the other is three steps: position → timestamp → chunk.

```js
export function chunkFor(ts, run, chunkSeconds = PLAYBACK_CHUNK_SECONDS) {
  const end = Math.min(ts + chunkSeconds, run.end);
  let start = ts;
  if (end - start < 1) start = Math.max(run.start, end - 1);
  if (end - start <= 0) return null;               // run too short to play
  return { start, end };
}
```

Two clamps, and both exist because of a specific real click:

**`Math.min(ts + chunkSeconds, run.end)`** — a click 30 seconds before a run ends should play 30 seconds, not request five minutes that mostly fall inside the following gap. Lesson 12 told you what happens to a range containing no fragments: `ResourceNotFoundException` → a 404. Asking for footage you can see isn't there is a request you should never send.

**The `< 1` nudge** — a click on the last pixel of a bar produces `ts === run.end`, so `end - start === 0`, and Lesson 12's validator rejects that with a 400. That's a routine click landing on a legitimate target, so it must not depend on the backend's error path. Pulling `start` back by a second (never past `run.start`) turns it into a normal request.

This is worth stating as a principle, because it generalizes past this project: **the backend's validation is a boundary, not a UI design**. `/api/hls` returning 400 for a zero-length range is correct and must stay; a UI that routinely triggers it for ordinary clicks is not.

## Step 2 — Wiring the click, and the gap that needs no code

```js
function init() {
  el("track").addEventListener("click", (e) => {
    if (!e.target.classList.contains("run")) return;   // clicks on gaps do nothing
    const rect = el("track").getBoundingClientRect();
    const ts = timestampAt((e.clientX - rect.left) / rect.width, state.win);
    playFrom(ts, { start: +e.target.dataset.start, end: +e.target.dataset.end });
  });

  el("track").addEventListener("keydown", (e) => {
    if (!e.target.classList.contains("run")) return;
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    const run = { start: +e.target.dataset.start, end: +e.target.dataset.end };
    playFrom(run.start, run);
  });

  el("record-btn").addEventListener("click", toggleRecording);
  el("video").addEventListener("timeupdate", movePlayhead);

  poll();
  setInterval(poll, POLL_MS);
}
```

Acceptance criterion #5 says *"clicking a gap does nothing and issues no request."* Look at how much code implements it: one early `return`, and only because the listener is on the track rather than on each bar. Because Lesson 14 decided gaps aren't elements, a click on a gap has nothing to hit — `e.target` is the track itself, the guard returns, and no request is made. A design where gaps *were* elements would need this same guard plus a decision about what a gap's own click handler should do.

The single listener on the track (rather than one per bar) is called event delegation, and it matters here for a specific reason: `renderTimeline` destroys and rebuilds every bar on every 10-second poll. Per-bar listeners would need re-attaching each time, and any bar you were interacting with would silently lose its handler mid-poll.

Note `timestampAt` uses `getBoundingClientRect()` — the track's *actual rendered* width, whatever the window size. That's the payoff of Lesson 14's decision to position everything in percentages: the click math needs no knowledge of layout at all.

Keyboard activation plays from the run's **start**, not from a click position — there isn't one. That's the right default anyway: someone tabbing to a run wants to watch it, and the whole run is what they selected.

## Step 3 — Requesting the URL

```js
async function playFrom(ts, run) {
  const chunk = chunkFor(ts, run);
  if (!chunk || state.inFlight) return;
  state.inFlight = true;
  showOverlay("<span>Loading…</span>");
  el("chunk-badge").hidden = false;
  el("chunk-badge").textContent = `${fmtClock(chunk.start)} — ${fmtClock(chunk.end)}`;

  try {
    const r = await fetch(`/api/hls?start=${chunk.start}&end=${chunk.end}`);
    const body = await r.json();
    if (!r.ok) {
      console.error("hls request failed", r.status, body);
      showOverlay("<span><strong>Playback failed for this segment.</strong>" +
                  "Pick another moment, or try again.</span>");
      return;
    }
    state.chunk = chunk;
    loadHls(body.url);
    hideOverlay();
  } catch (err) {
    console.error("hls request failed", err);
    showOverlay("<span><strong>Playback failed for this segment.</strong>" +
                "Pick another moment, or try again.</span>");
  } finally {
    state.inFlight = false;
  }
}
```

**`state.inFlight`** prevents a second request while one is outstanding. Without it, dragging across the timeline fires a request per click, each minting a fresh HLS session, and whichever resolves last wins regardless of what you clicked last.

**The error copy is the spec's, deliberately.** Section 6.3: *"'Playback failed for this segment' plus a retry affordance. Do not surface AWS error codes to the viewer; log them to console."* So the viewer gets a sentence describing their situation and what to do; `console.error` gets the status code and the response body. The person who needs `ResourceNotFoundException` is you, in devtools — not someone trying to look at a hallway.

**The timeline stays interactive while loading.** The overlay covers the player, not the page (spec 6.3), so a slow chunk never blocks picking a different one.

**Mint fresh, never reuse.** Every seek calls `/api/hls` again. These URLs carry an expiring session token (`Expires=300`, Lesson 12), so caching one buys a stale URL and a bug that only appears after five minutes of use. One request per seek is both simpler and correct.

## Step 4 — hls.js, and why the `<video>` tag isn't enough

HLS is a playlist format: a `.m3u8` file listing short media segments, refreshed as playback advances. Safari plays it natively. Chrome and Firefox do not — a bare `<video src="…m3u8">` there simply fails. hls.js fills the gap by fetching the playlist and segments itself and feeding them to the video element through Media Source Extensions.

```js
function loadHls(url) {
  const video = el("video");
  if (window.Hls && window.Hls.isSupported()) {
    if (state.hls) state.hls.destroy();
    state.hls = new window.Hls();
    state.hls.on(window.Hls.Events.ERROR, (_e, data) => {
      if (!data.fatal) return;
      console.error("hls.js fatal error", data);
      showOverlay("<span><strong>Playback failed for this segment.</strong>" +
                  "Pick another moment, or try again.</span>");
    });
    state.hls.loadSource(url);
    state.hls.attachMedia(video);
    video.play().catch(() => {});
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;               // Safari plays HLS natively
    video.play().catch(() => {});
  } else {
    showOverlay("<span><strong>Playback failed for this segment.</strong>" +
                "This browser cannot play HLS.</span>");
  }
}
```

- **`destroy()` before creating a new instance.** Each `Hls` object holds buffers, timers, and network requests. Seek ten times without destroying and you have ten instances all still fetching segments — memory climbs, and stale ones can push data into the same video element.
- **`if (!data.fatal) return;`** — hls.js emits non-fatal errors routinely (a segment that needed a retry, a gap it recovered from). Surfacing those would flash a failure message during successful playback.
- **`.catch(() => {})` on `play()`** — browsers reject autoplay with sound and the rejection is expected, not exceptional. The user presses play; the controls are right there.
- **Order matters:** `loadSource` then `attachMedia`. Reversed, hls.js may attach to the element before it knows what to load.
- The `<script src="…hls.js">` tag is a plain script, not a module, and must come **before** `app.js` so `window.Hls` exists when the module runs.

Notice what this function never does: proxy video through your server. The browser fetches segments straight from AWS using the signed URL. Your backend mints URLs and nothing more — which is why a Python server that never touches video can serve smooth playback.

## Step 5 — The playhead

```js
function movePlayhead() {
  const playhead = el("playhead");
  if (!state.chunk) { playhead.hidden = true; return; }
  const at = state.chunk.start + el("video").currentTime;
  const pct = pctOf(at, state.win);
  playhead.hidden = pct < 0 || pct > 100;
  playhead.style.left = pct + "%";
}
```

`video.currentTime` is seconds **into the loaded chunk**, so absolute position is `chunk.start + currentTime`. Then it's `pctOf` again — the same conversion the bars and ticks use, which is why the playhead lands exactly on the footage it's playing.

Two things this is careful about:

- **It's driven by `timeupdate`, not a timer.** A `setInterval` that advances the playhead by wall-clock time would drift away from actual playback the moment the video buffers, pauses, or the user scrubs. The video element is the source of truth about where playback is; asking it is both simpler and correct.
- **It hides itself when out of range.** The window slides every 10 seconds. Watch a chunk long enough and its moment falls off the left edge — at which point `pct < 0` and the playhead hides rather than pinning to the edge and implying playback is somewhere it isn't.

## Step 6 — The recording control

The spec is emphatic on one point: *"Every displayed state comes from a server response."* The button never guesses.

```js
export function buttonStateFor(s) {
  const external = s.running && !s.managed;
  return {
    label: s.running ? "Stop" : "Start",
    action: s.running ? "stop" : "start",
    statusText: s.running ? "recording" : "stopped",
    title: external ? "This agent was started outside the server — stop it where it was started." : "",
    note: external
      ? "Agent started externally; it is reported here but not controlled from this page."
      : null,
  };
}

function applyRecordingState(s) {
  const view = buttonStateFor(s);
  const btn = el("record-btn");
  const note = el("record-note");
  el("status").dataset.running = String(s.running);
  el("status-text").textContent = view.statusText;
  btn.disabled = false;
  btn.textContent = view.label;
  btn.dataset.action = view.action;
  btn.title = view.title;
  note.hidden = view.note === null;
  if (view.note) note.textContent = view.note;
}

async function toggleRecording() {
  const btn = el("record-btn");
  const action = btn.dataset.action;
  btn.disabled = true;
  btn.textContent = action === "start" ? "Starting…" : "Stopping…";
  try {
    const r = await fetch(`/api/recording/${action}`, { method: "POST" });
    const body = await r.json();
    if (!r.ok) {
      el("record-note").hidden = false;
      el("record-note").textContent = body.detail || "Could not change recording state.";
      await pollRecording();
      return;
    }
    applyRecordingState(body);
  } catch (err) {
    console.error("recording toggle failed", err);
    await pollRecording();
  }
}
```

`buttonStateFor` is a pure function — server state in, display decisions out — for the same reason Lesson 14's geometry was: it's the part with actual logic in it, so it's the part worth testing without a browser.

Four rules it encodes:

**Status reflects the agent, not the archive.** `● recording` comes from `/api/recording`, never from "are there fragments?" Old footage exists long after recording stops; deriving the light from the archive would show `recording` for 24 hours after the agent died.

**Optimism is not allowed.** After a POST, the display comes from the POST's own response body. Between POSTs, it comes from the same 10-second poll that refreshes the timeline. So an agent that crashes on its own, or that you stop in another terminal, corrects itself within one cycle rather than showing a state that stopped being true.

**In-flight is a visible state.** Disable the button and change the label to `Starting…`/`Stopping…`. A `stop` that escalates through `SIGTERM` → 15 seconds → `SIGKILL` (Lesson 6) can genuinely take fifteen seconds, and a button that looks idle for fifteen seconds gets pressed again.

**The 409 is a message, not a status code.** Lesson 6's `NotManaged` becomes Lesson 13's `HTTPException(409, …)`, and lands here as `body.detail` — displayed as a sentence. The viewer never sees "409"; they see why the button didn't work.

```js
async function pollRecording() {
  try {
    const r = await fetch("/api/recording");
    applyRecordingState(await r.json());
  } catch (err) {
    console.error("recording poll failed", err);
  }
}
```

Call `pollRecording()` at the end of `poll()` so both halves of the interface refresh on the same 10-second cycle.

## Step 7 — Test the interaction logic

As in Lesson 14, the browser-free parts get tested without a browser. Copy `app.js` to `app.mjs` and:

```js
// interaction_test.mjs
import { windowFor, runAt, chunkFor, buttonStateFor } from "./app.mjs";

let pass = 0;
const ok = (c, l) => { if (!c) { console.error("FAIL:", l); process.exitCode = 1; } else pass++; };

const NOW = 1788030000;
const win = windowFor(NOW, 60);
const runs = [
  { start: win.start + 300,  end: win.start + 900  },
  { start: win.start + 1800, end: win.start + 3000 },
];

// --- which run did the click land in? ---
ok(runAt(win.start + 600, runs) === runs[0], "a click inside a run finds that run");
ok(runAt(win.start + 1200, runs) === null, "a click in a gap finds nothing — no request is made");
ok(runAt(win.start + 300, runs) === runs[0], "a run's exact start counts as inside it");
ok(runAt(win.start + 900, runs) === runs[0], "a run's exact end counts as inside it");
ok(runAt(win.start + 100, runs) === null, "a click before the first run finds nothing");

// --- what chunk should be requested? ---
const full = chunkFor(win.start + 1800, runs[1], 300);
ok(full.start === win.start + 1800 && full.end === win.start + 2100,
   "a click with room to spare requests the full 300s chunk");

const clamped = chunkFor(win.start + 2900, runs[1], 300);
ok(clamped.end === runs[1].end, "a chunk is clamped to the end of its run, never past it");
ok(clamped.end - clamped.start === 100, "the clamped chunk is exactly the footage that remains");

const atEnd = chunkFor(runs[1].end, runs[1], 300);
ok(atEnd !== null && atEnd.end - atEnd.start >= 1,
   "clicking the last pixel of a run still yields a playable chunk, not a guaranteed 400");
ok(atEnd.start >= runs[1].start, "the nudge never reaches back before the run started");

const short = chunkFor(1000, { start: 1000, end: 1000.4 }, 300);
ok(short !== null && short.end - short.start > 0,
   "a sub-second run yields the whole run, a positive duration the backend accepts");
ok(chunkFor(1000, { start: 1000, end: 1000 }, 300) === null,
   "a zero-length run is the only case that yields no chunk at all");

// every chunk this UI can produce must satisfy the backend's own rule from Lesson 12
for (const t of [0, 1, 299, 600, 1199, 1200]) {
  const c = chunkFor(runs[1].start + t, runs[1], 300);
  if (c === null) continue;
  const d = c.end - c.start;
  ok(d > 0 && d <= 300, `chunk at +${t}s satisfies 0 < d <= 300 (got ${d})`);
}

// --- what should the button say? ---
const stopped = buttonStateFor({ running: false, managed: false, pid: null });
ok(stopped.label === "Start" && stopped.action === "start", "a stopped agent offers Start");
ok(stopped.statusText === "stopped" && stopped.note === null, "stopped shows no external-agent note");

const managed = buttonStateFor({ running: true, managed: true, pid: 4242 });
ok(managed.label === "Stop" && managed.action === "stop", "a managed running agent offers Stop");
ok(managed.note === null, "a managed agent shows no note");

const external = buttonStateFor({ running: true, managed: false, pid: 9001 });
ok(external.label === "Stop", "an external agent still shows Stop (the 409 explains why it fails)");
ok(external.note !== null && external.title !== "", "an external agent explains itself in a note and tooltip");
ok(external.statusText === "recording", "status reflects the agent, not who owns it");

console.log(`${pass} interaction assertions passed`);
```

```bash
node interaction_test.mjs
```

```
25 interaction assertions passed
```

The loop in the middle is the one to notice. It asserts that **every chunk this UI can produce satisfies the backend's own validation rule** — the contract between Lesson 12 and Lesson 15, checked mechanically instead of by reading both files and hoping. That's the kind of assertion that keeps working after someone changes `PLAYBACK_CHUNK_SECONDS` in six months.

## Step 8 — Run the whole thing

Everything is built. Now run it as a system:

```bash
make setup      # preflight + stream provisioning (Lesson 13)
make serve      # leave running
# open http://localhost:8000
```

Press **Start**. What should happen, in order:

1. The button reads `Starting…` and disables.
2. Within a second it returns as `Stop`, and the status strip reads `● recording`.
3. The timeline stays empty for **roughly 10–30 seconds**. This is expected and is the single most common false alarm in this project: fragments need time to be ingested, indexed, and become listable. Nothing is broken.
4. A bar appears at the right edge and grows leftward-anchored as the window slides.
5. Click it. The overlay reads `Loading…`, then footage plays, and the playhead appears in the signal colour at the moment you clicked.

Then press **Stop**, wait a minute, press **Start** again, and watch a gap appear in the timeline exactly where the agent wasn't running. That gap is the whole system working: GStreamer paced the clip in real time, `kvssink` published fragments with producer timestamps, `ListFragments` returned them, the server merged them into runs using the 1-second rule, and the frontend rendered the absence between them.

## Step 9 — The acceptance criteria

The spec wrote thirteen of these before any code existed. Work through them honestly — the point is finding out which fail.

| # | Criterion | How to check |
|---|---|---|
| 1 | 10 minutes of running produces ~10 minutes of footage, not 10 seconds | **Check this first.** `make stream` for 10 min, then read the coverage line in the meta row. If it says seconds, `identity sync=true` is missing or misplaced (Lesson 10). |
| 2 | `/api/fragments` returns multiple runs with sub-second gaps at loop boundaries | `curl` it after two loop cycles; the gaps come from process-restart looping (Lesson 10). |
| 3 | Timeline renders those runs with gaps at correct positions | Compare bar positions against the JSON timestamps and the clock labels. |
| 4 | Clicking a bar plays from that moment ±2s | Click a labeled quarter-hour tick's bar; check the chunk badge. Off by 2–5s consistently means a `ServerTimestamp`/`ProducerTimestamp` mix (Lesson 11). |
| 5 | Clicking a gap does nothing, issues no request | Click a gap with the Network tab open. Zero requests. |
| 6 | Killing the agent 60s and restarting shows a 60s gap within one poll | Kill it, wait, restart, wait 10s. |
| 7 | `/api/hls` with a 3600s range returns 400 naming the limit | `curl` directly — the UI can't produce this, by Step 1's clamping. |
| 8 | `/api/hls` inside a known gap returns 404 with a readable message | Read a gap's timestamps off `/api/fragments`, then `curl` a range inside it. |
| 9 | No AWS credential in any response or in page source | View source; search the Network tab. Structurally guaranteed by Lesson 13's config split, but verify. |
| 10 | Under ~600 lines excluding HTML/CSS | `wc -l`. See the note below. |
| 11 | Stop leaves **zero** containers and no further fragment writes | `docker ps` **and** the fragment count — not the UI. A UI reading "stopped" is exactly what an orphaned container hides behind (Lesson 8). |
| 12 | Start → Stop → Start three times, no `name already in use`, no accumulated containers | Do it three times; `docker ps -a` after. |
| 13 | Stop on an externally-started agent returns 409 and leaves it running | `make stream` in another terminal, then press Stop. |

**On criterion #10.** The spec records its own status as *not met* — roughly 805 code lines against a ~600 budget — and names the overage precisely: the Docker route, the recording supervisor, and the preflight fallbacks, none of which were in the original scope. It's worth understanding why that's written down rather than quietly dropped. The budget existed to keep the thing readable, and a budget you silently revise every time you exceed it isn't a constraint, it's a decoration. Recording the miss and its cause keeps the pressure on whatever gets added next — which is the only thing the budget was ever for.

Your build will land somewhere similar. Measure it, write down the number, and don't adjust the target to match.

Criteria #11 and #13 are the two most worth doing carefully, because both are cases where **the interface tells you the truth and reality disagrees.** #11 is the orphaned container: the supervisor exited, the UI says stopped, and a container is still publishing to AWS on your bill. #13 is the reverse: the UI correctly refuses to stop something it doesn't own. Both are the same underlying lesson — a status display is a claim about the world, and the only way to know it's true is to check the world.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Click does nothing, no request in the Network tab | You clicked a gap (correct behavior), or `e.target` isn't the bar — check the `.run` class guard and that `.playhead` has `pointer-events: none`. |
| `Hls is not defined` | The hls.js `<script>` is after `app.js`, or the CDN is blocked. It must load first; `app.js` is a module and defers by default. |
| Video element stays black, no error | Playback started but the chunk has no keyframe near its start. Click slightly earlier in the same run. |
| Playback stalls at the end of every chunk | Expected in this MVP — chunks are discrete with no rollover. Documented in the spec's known failure modes. |
| Playhead is in the wrong place but playback is right | `movePlayhead` using wall-clock time instead of `chunk.start + video.currentTime`. |
| Playhead drifts a few seconds from the bar it's playing | `ServerTimestamp` used in one endpoint and `ProducerTimestamp` in the other (Lesson 11's warning). |
| Memory climbs the more you seek | `state.hls.destroy()` not called before creating a new instance. |
| Button flickers between Start and Stop | Both the POST response and a poll are applying state out of order — make sure `toggleRecording` awaits its own response before the next poll can overwrite it. |
| Stop reports success but `docker ps` shows a container | The orphaned-container failure (Lesson 8) — the supervisor must `docker rm -f` by name on shutdown. |
| Everything works, then all requests 404 after an edit | `app.mount("/", ...)` moved above the API routes (Lesson 13). |

## Recap

- A click becomes a chunk via two clamps: never past the run's end, and never shorter than a second — so the UI never routinely triggers the backend's own 400.
- The backend's validation is a boundary, not a substitute for a UI that sends sensible requests.
- Clicking a gap is ignored by one `return`, because Lesson 14 decided gaps aren't elements.
- Event delegation on the track survives the 10-second re-render that destroys every bar.
- hls.js plays HLS where browsers can't; `destroy()` before each new instance, ignore non-fatal errors, and let the browser fetch segments straight from AWS rather than proxying video through your server.
- The playhead follows `video.currentTime`, not a timer, and hides itself when its moment slides out of the window.
- Every displayed recording state comes from a server response — the POST's body or the 10-second poll — so a crashed or externally-stopped agent self-corrects within one cycle.
- Status reflects the agent, never the archive.
- A `409` reaches the viewer as a sentence; AWS error codes reach the console only.
- Acceptance criteria are checked against the world (`docker ps`, fragment counts), not against the interface's own claims — and a missed criterion gets recorded, not quietly relaxed.

## Exercises

1. Click the very last pixel of a bar with the Network tab open. Confirm the request's `start` is about a second before the run's end and that it returns 200 — then remove the `< 1` nudge from `chunkFor` and watch the same click produce a 400. That single line is what stands between a normal click and an error state.
2. Seek ten times in a row with devtools' Memory panel open, then comment out `state.hls.destroy()` and do it again. Compare.
3. Start the agent from the UI, then run `make stream` in a second terminal so two agents exist. Read what `/api/recording` reports and reason about why `managed` is the field that makes the answer coherent. Then stop both cleanly.
4. Make `/api/hls` fail on purpose — point `STREAM_NAME` at a stream that doesn't exist and restart the server. Click a bar. Confirm the viewer sees "Playback failed for this segment" and the console shows the real `ResourceNotFoundException`. That split is the whole of the spec's error-presentation rule.
5. Run acceptance criterion #11 properly: press Stop, then check `docker ps` **and** re-request `/api/fragments` a minute later to confirm the fragment count stopped growing. Write down which of the two you'd have trusted if you'd only checked the UI.
6. Measure criterion #10 with `wc -l` across `server/`, `edge/`, `scripts/`, and `web/app.js`. Write the number in your README next to the target, along with one sentence on what the overage bought.

## Where this is going

Nowhere — this is the end of the build. The system runs: a simulated camera paced in real time by GStreamer, published to Kinesis Video Streams through a compiled sink in a container, archived and queried through boto3, merged into runs by a FastAPI backend, and rendered as a timeline you can click to watch any moment it holds.

Worth noticing what the fifteen lessons actually did. Every one of them ran something real at every step — a process you could signal, a container you could inspect, a pipeline whose output you could play, a cached client whose call count you could count, a page you could look at — and each lesson replaced a stand-in from the one before: `camera_sim.py` became the real pipeline, `filesink` became `kvssink`, fake clients became boto3, fixtures became real fragments. That's not a teaching gimmick; it's how you build something with this many unfamiliar parts without ever being more than one layer away from something you can verify. The spec calls for a reference implementation meant to be read. You now have one, and you know why each part of it is the way it is.
