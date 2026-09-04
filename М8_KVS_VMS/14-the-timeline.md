# Lesson 14 — The Timeline

**Module:** The Frontend & Assembly (Module 7)
**You will build:** `web/index.html`, `web/style.css`, and the geometry half of `web/app.js` — a surveillance-console timeline that renders real runs and real gaps from the server you assembled in Lesson 13.
**Time:** ~90 minutes.

## Why this lesson exists

The spec is unusually direct about this one screen: *"the signature element is the timeline itself… this is the one place to spend effort; everything else stays quiet."* That's a design instruction, but it's also an engineering one. A timeline is a coordinate system — it maps a range of *time* onto a range of *pixels*, and every interaction in the next lesson (clicking to seek, tracking the playhead) is that same mapping run forwards or backwards. Get the mapping right here and the rest of the interface is small. Get it wrong and every symptom shows up somewhere else, disguised as a playback bug.

So this lesson separates the two things that usually get tangled in frontend code: the **arithmetic** (pure functions of numbers, testable without a browser) and the **rendering** (DOM, CSS, and how it actually looks). You'll test the first with `node` and verify the second by looking at it.

## Prerequisites

- Lesson 13 — a running `make serve` with `/api/fragments` answering.
- Basic JavaScript: `const`/`let`, arrow functions, `fetch`, `async/await`, array methods. No framework experience needed; there is no framework.
- For the development loop: run the server with `VMS_FIXTURES=1` (Lesson 13, Step 7) so the timeline has runs to draw before you've published any real footage.

## Learning objectives

1. Build the sliding time window and convert timestamps to percentage positions in both directions.
2. Render runs as absolutely-positioned bars, with gaps as the background showing through — and explain why gaps are never elements.
3. Handle the four geometry edge cases that appear the moment real data arrives: runs that overflow either end of the window, runs entirely outside it, and runs too small to see.
4. Draw a tick ruler aligned to real clock minutes, not to window-relative offsets.
5. Implement the three interface states (empty, loading, error) and the `[hidden]` trap that breaks them.
6. Poll every 10 seconds and re-render, so the window slides and always ends at "now".

---

## Step 1 — The window, and one conversion function

Everything on this screen is derived from a single idea: a **window** of time, `TIMELINE_WINDOW_MINUTES` long, that always ends at the current moment.

```js
// web/app.js
// Duplicated from .env — there is no config endpoint. If you change one, change both.
const TIMELINE_WINDOW_MINUTES = 60;
const PLAYBACK_CHUNK_SECONDS = 300;
const POLL_MS = 10000;

export function windowFor(nowSec, minutes = TIMELINE_WINDOW_MINUTES) {
  return { start: nowSec - minutes * 60, end: nowSec };
}

export function pctOf(ts, win) {
  return ((ts - win.start) / (win.end - win.start)) * 100;
}

export function timestampAt(fraction, win) {
  return win.start + fraction * (win.end - win.start);
}
```

`pctOf` maps a timestamp to a horizontal position; `timestampAt` maps a position back to a timestamp. They are exact inverses, and between them they are the entire coordinate system of this interface. Percentages rather than pixels, deliberately: the browser recomputes them on every resize for free, so a resized window needs no JavaScript at all.

Note that these are **exported, browser-free functions**. That's not ceremony — it's what lets you test the arithmetic in Step 7 without a DOM, a server, or a browser.

## Step 2 — Runs are bars; gaps are nothing

The server hands you `runs` — the merged, contiguous stretches from Lesson 12. Each becomes one absolutely-positioned element:

```js
export function barGeometry(run, win) {
  const left = Math.max(0, pctOf(run.start, win));
  const right = Math.min(100, pctOf(run.end, win));
  if (right <= 0 || left >= 100) return null;             // entirely outside the window
  return { left, width: Math.max(right - left, 0.15) };   // floor so a 2s run stays visible
}
```

Four cases are handled in those four lines, and all four occur with real data within the first hour:

- **A run inside the window** — `left` and `right` both land in 0–100, nothing clamps.
- **A run that started before the window** — the agent has been recording for two hours; the first run extends off the left edge. `Math.max(0, …)` clamps it, so the bar starts at the window's edge rather than at a negative offset (which the browser would happily render *outside* the track).
- **A run still in progress** — its end is `now`, or a moment past it if a fragment landed between the request and the render. `Math.min(100, …)` clamps it.
- **A run entirely outside the window** — returns `null`, and the caller skips it. Without this, a run from three hours ago becomes a zero- or negative-width element sitting at a nonsense position.

The `Math.max(…, 0.15)` floor matters more than it looks. Over a 60-minute window, one second of footage is 0.028% of the track — about a third of a pixel at 1200px wide, which rounds away to nothing. A short recording would exist in the data and be invisible on screen, which is the worst possible outcome for an interface whose whole job is showing you what exists. The floor guarantees a hairline.

**Gaps are not elements.** There is no gap object, no `.gap` class, no loop over the spaces between runs. A gap is simply the track's own background where no bar was drawn. This is the single most useful structural decision on this screen: gaps require no code, can never disagree with the runs around them, and — the reason it matters in Lesson 15 — a click that lands on a gap hits the track, not a bar, so "clicking a gap does nothing" needs no special case either.

## Step 3 — A ruler on real clock minutes

The spec asks for hairline minute ticks, heavier five-minute ticks, and labeled quarter-hours. The subtlety is *where* the ticks go:

```js
export function tickMarks(win) {
  const marks = [];
  const first = Math.ceil(win.start / 60) * 60;     // first whole minute inside the window
  for (let ts = first; ts <= win.end; ts += 60) {
    const minute = new Date(ts * 1000).getMinutes();
    marks.push({ ts, major: minute % 5 === 0, labeled: minute % 15 === 0 });
  }
  return marks;
}

export function fmtClock(ts) {
  const d = new Date(ts * 1000);
  return String(d.getHours()).padStart(2, "0") + ":" +
         String(d.getMinutes()).padStart(2, "0");
}
```

The window slides continuously — it starts at whatever "now minus 60 minutes" happens to be, which is essentially never a whole minute. So ticks cannot be evenly divided across the track; they must be placed at real clock instants, which means the first one sits slightly *inside* the left edge and the whole ruler drifts left between polls. `Math.ceil(win.start / 60) * 60` is what finds that first whole minute.

Divide the track into 60 equal parts instead and everything still *looks* fine — until you notice the label says `18:15` and the tick under it is 40 seconds off. That's the kind of error nobody reports and everybody quietly distrusts.

## Step 4 — Rendering

```js
const el = (id) => document.getElementById(id);

const state = {
  win: windowFor(Date.now() / 1000),
  runs: [],
  chunk: null,      // Lesson 15
  hls: null,        // Lesson 15
  inFlight: false,
};

function renderTimeline() {
  const track = el("track");
  const labels = el("labels");
  const playhead = el("playhead");

  // Rebuild ticks and bars; keep the playhead node itself.
  [...track.children].forEach((c) => { if (c !== playhead) c.remove(); });
  labels.textContent = "";

  for (const mark of tickMarks(state.win)) {
    const t = document.createElement("div");
    t.className = mark.major ? "tick major" : "tick";
    t.style.left = pctOf(mark.ts, state.win) + "%";
    track.appendChild(t);
    if (mark.labeled) {
      const p = pctOf(mark.ts, state.win);
      const s = document.createElement("span");
      s.textContent = fmtClock(mark.ts);
      // Centre the label on its tick, except at the very edges, where a centred
      // label would hang outside the track and force the page to scroll sideways.
      if (p < 4) { s.style.left = "0%"; s.style.transform = "translateX(0)"; }
      else if (p > 96) { s.style.left = "100%"; s.style.transform = "translateX(-100%)"; }
      else { s.style.left = p + "%"; }
      labels.appendChild(s);
    }
  }

  for (const run of state.runs) {
    const geo = barGeometry(run, state.win);
    if (!geo) continue;
    const bar = document.createElement("button");
    bar.className = "run";
    bar.style.left = geo.left + "%";
    bar.style.width = geo.width + "%";
    bar.dataset.start = run.start;
    bar.dataset.end = run.end;
    bar.setAttribute(
      "aria-label",
      `Recording from ${fmtClock(run.start)} to ${fmtClock(run.end)}. Press Enter to play.`
    );
    track.appendChild(bar);
  }

  el("window-label").textContent = `${fmtClock(state.win.start)} — ${fmtClock(state.win.end)}`;
  const secs = Math.round(totalCoverage(state.runs));
  el("coverage-label").textContent = state.runs.length
    ? `${state.runs.length} run${state.runs.length > 1 ? "s" : ""} · ${Math.floor(secs / 60)}m ${secs % 60}s of footage`
    : "";
}

export function totalCoverage(runs) {
  return runs.reduce((sum, r) => sum + (r.end - r.start), 0);
}
```

Two choices worth defending:

**Each bar is a `<button>`, not a `<div>`.** The spec's quality floor requires keyboard-focusable runs with a visible focus ring. A `<button>` gets focusability, `Enter`/`Space` activation, and screen-reader semantics from the browser; a `<div>` needs `tabindex`, key handlers, and an ARIA role bolted on to imitate the same thing badly. Choosing the element that already means "activatable thing" is most of accessibility on this page.

**The label clamp at the edges.** A label centred on a tick at 100% hangs half its width past the track, which pushes the document wider than the viewport and gives the whole page a horizontal scrollbar. On a 380px phone that's the difference between a usable page and a broken one. Two `if`s at the boundary; nothing else needed.

## Step 5 — States, and the `[hidden]` trap

```js
function showOverlay(html) {
  const o = el("overlay");
  o.innerHTML = html;
  o.hidden = false;
}
function hideOverlay() { el("overlay").hidden = true; }
```

The spec is specific that an empty archive is **not an error state**:

> **Empty archive:** "No recording yet. Press Start to begin." Not an error state — an instruction.

That distinction is worth taking seriously. A new user's first load has an empty archive every single time; greeting them with something that looks like a failure teaches them the tool is broken before they've used it. An empty archive is the normal beginning of the normal path, and the copy should say what to do next.

Now the trap, which will cost you twenty minutes if you meet it undocumented:

```css
[hidden] { display: none !important; }
```

The browser's own stylesheet has `[hidden] { display: none }`, but that's a *user-agent* rule — the weakest kind. The moment you write `.overlay { display: flex }` in your own stylesheet, your author rule wins, and `overlay.hidden = true` sets an attribute that changes nothing at all. The panel simply never hides, and the bug reads as "my JavaScript isn't running." Any element whose visibility you drive with `hidden` and which also carries an author `display` needs this rule.

## Step 6 — Polling

```js
async function poll() {
  state.win = windowFor(Date.now() / 1000);
  try {
    const r = await fetch(`/api/fragments?start=${state.win.start}&end=${state.win.end}`);
    if (!r.ok) throw new Error(`fragments ${r.status}`);
    const data = await r.json();
    state.runs = data.runs;
  } catch (err) {
    console.error("fragments poll failed", err);
  }
  renderTimeline();

  if (!state.runs.length && !state.chunk) {
    showOverlay("<span><strong>No recording yet.</strong>Press Start to begin.</span>");
  } else if (!state.chunk) {
    hideOverlay();
    showOverlay("<span>Click the timeline to play from that moment.</span>");
  }
}

function init() {
  poll();
  setInterval(poll, POLL_MS);
}

if (typeof document !== "undefined") init();
```

Three details:

- `state.win` is recomputed at the **top of every poll**, not once at startup. That's what makes the window slide: ten seconds later it covers a range ten seconds later, and the whole ruler shifts left by 0.28% of the track.
- A failed fetch logs and re-renders with the **previous** runs rather than blanking the timeline. A transient network blip should not erase the picture; the next poll fixes it ten seconds later.
- `if (typeof document !== "undefined") init();` lets the same file be imported by `node` for testing without trying to touch a DOM that isn't there. It costs one line and it's what makes Step 7 possible.

## Step 7 — Test the arithmetic without a browser

Every function in Steps 1–3 is a pure function of numbers, so it can be tested directly. Copy `app.js` to `app.mjs` (so `node` treats it as a module) and write:

```js
// geo_test.mjs
import { windowFor, pctOf, barGeometry, timestampAt, tickMarks, totalCoverage } from "./app.mjs";

let pass = 0;
const ok = (cond, label) => {
  if (!cond) { console.error("FAIL:", label); process.exitCode = 1; } else { pass++; }
};

const NOW = 1788030000;                  // a fixed instant, so every assertion is deterministic
const win = windowFor(NOW, 60);
ok(win.end === NOW && win.start === NOW - 3600, "window is the last 60 minutes, ending now");

ok(pctOf(win.start, win) === 0, "window start is 0%");
ok(pctOf(win.end, win) === 100, "window end is 100%");
ok(pctOf(win.start + 1800, win) === 50, "halfway is 50%");
ok(timestampAt(0.25, win) === win.start + 900, "25% across the track is 15 minutes in");

const mid = barGeometry({ start: win.start + 900, end: win.start + 1800 }, win);
ok(mid.left === 25 && mid.width === 25, "a run over the 2nd quarter is left=25 width=25");

const spillLeft = barGeometry({ start: win.start - 600, end: win.start + 900 }, win);
ok(spillLeft.left === 0, "a run starting before the window clamps to left=0");

const spillRight = barGeometry({ start: win.end - 900, end: win.end + 600 }, win);
ok(Math.abs(spillRight.left - 75) < 1e-9 && Math.abs(spillRight.width - 25) < 1e-9,
   "a run ending after the window clamps to 100");

ok(barGeometry({ start: win.start - 900, end: win.start - 600 }, win) === null,
   "a run entirely before the window renders nothing");
ok(barGeometry({ start: win.end + 60, end: win.end + 120 }, win) === null,
   "a run entirely after the window renders nothing");
ok(barGeometry({ start: win.start + 100, end: win.start + 102 }, win).width === 0.15,
   "a 2-second run still gets a visible minimum width");

const marks = tickMarks(win);
ok(marks.every((m) => m.ts >= win.start && m.ts <= win.end), "every tick falls inside the window");
ok(marks.every((m) => new Date(m.ts * 1000).getSeconds() === 0),
   "ticks land on real clock minutes, not on window-relative offsets");
ok(marks.filter((m) => m.labeled).every((m) => new Date(m.ts * 1000).getMinutes() % 15 === 0),
   "only quarter-hours are labeled");
ok(marks.filter((m) => m.major).every((m) => new Date(m.ts * 1000).getMinutes() % 5 === 0),
   "only five-minute marks are major");

ok(totalCoverage([{ start: 0, end: 600 }, { start: 900, end: 2100 }]) === 1800,
   "coverage sums every run's duration");
ok(totalCoverage([]) === 0, "an empty archive has zero coverage");

console.log(`${pass} geometry assertions passed`);
```

```bash
node geo_test.mjs
```

```
17 geometry assertions passed
```

This is worth doing even though it feels like overkill for a page with no framework. Every one of those assertions describes a case that produces a *visually plausible but wrong* timeline — a bar in the wrong place looks like a bar, and you will believe it. The arithmetic is the part you cannot check by looking, so it's the part worth checking mechanically.

## Step 8 — The markup and the look

```html
<!-- web/index.html -->
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cam-01 — Cloud VMS</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<main class="console">

  <div class="strip">
    <span class="stream-name" id="stream-name">cam-01</span>
    <span class="status" id="status" data-running="false">
      <span class="dot"></span><span id="status-text">connecting…</span>
    </span>
    <span class="spacer"></span>
    <button class="control" id="record-btn" disabled>—</button>
    <span class="note" id="record-note" hidden></span>
  </div>

  <div class="stage">
    <video id="video" controls playsinline></video>
    <span class="badge" id="chunk-badge" hidden></span>
    <div class="overlay" id="overlay">
      <span><strong>No recording yet.</strong>Press Start to begin.</span>
    </div>
  </div>

  <div class="timeline">
    <div class="labels" id="labels"></div>
    <div class="track" id="track">
      <div class="playhead" id="playhead" hidden></div>
    </div>
    <div class="meta">
      <span id="window-label">—</span>
      <span id="coverage-label"></span>
    </div>
  </div>

</main>

<script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
<script type="module" src="app.js"></script>
</body>
</html>
```

Forty-six lines, and it is the whole interface. The status strip, player, and recording button are wired in Lesson 15; the markup is here because the layout is one composition and splitting it across two lessons would mean building it twice.

The palette and type are not defaults — the spec asks for a surveillance console, not a dashboard, and names the reasoning:

```css
:root {
  --ground:      #1A1D21;   /* desaturated cool grey, not black */
  --panel:       #202429;
  --track:       #23272C;
  --hairline:    #2E3339;
  --tick:        #343A41;
  --tick-strong: #4C555F;
  --bar:         #E8E4DC;   /* warm off-white: footage reads as presence, not accent */
  --signal:      #FF7A1A;   /* the playhead, and nothing else */
  --text:        #E8E4DC;
  --muted:       #868D96;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --sans: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
```

Recording bars in warm off-white rather than a saturated accent is the choice that sets the tone: footage-present reads as *material* — something physically there — rather than as a highlighted status. One signal colour, reserved for the playhead, is what makes the playhead mean something when it appears.

```css
.track {
  position: relative;
  height: 58px;
  background: var(--track);
  border: 1px solid var(--hairline);
  overflow: hidden;
}

.tick { position: absolute; top: 0; width: 1px; height: 7px; background: var(--tick); }
.tick.major { height: 13px; background: var(--tick-strong); }

.run {
  position: absolute;
  top: 16px; bottom: 8px;      /* leaves the ruler visible above every bar */
  background: var(--bar);
  cursor: pointer;
  border: none; padding: 0;
  min-width: 1px;
  display: block;
}
.run:focus-visible { outline: 2px solid var(--signal); outline-offset: 1px; z-index: 3; }

.playhead {
  position: absolute;
  top: 0; bottom: 0;
  width: 2px; margin-left: -1px;
  background: var(--signal);
  z-index: 4;
  pointer-events: none;
}

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
```

`.run` uses `top: 16px` so the bars sit *below* the ruler rather than covering it — the ticks stay readable across the full width, including behind footage, which is what makes gaps read as measured absences rather than styled dividers. And note there is no `:hover` rule on `.run` at all: the spec asks for motion on the playhead and nowhere else, so hover changes the cursor and nothing more. Stillness is what makes the one moving element mean something.

`pointer-events: none` on the playhead is small but load-bearing: without it, the playhead sits above the bars and swallows clicks aimed at the footage directly underneath it.

## Step 9 — Look at it

Start the server with fixtures so there's something to draw, and open it:

```bash
VMS_FIXTURES=1 make serve
# then open http://localhost:8000
```

You should see four runs separated by three clearly visible gaps, a dense ruler with labeled quarter-hours, and a meta line reading something like `18:02 — 19:02` and `4 runs · 45m 10s of footage`.

Now verify the things that are easy to get wrong and easy to miss:

1. **Watch it slide.** Leave the page open for 30 seconds. The ruler and bars should drift left as the window advances; the right edge always says "now."
2. **Resize the browser** from full width down to about 380px. Nothing should overflow horizontally — no scrollbar at the bottom of the page. The edge labels are what usually break this, which is what Step 4's clamp prevents.
3. **Tab to the timeline.** Each run should take focus in order with a visible orange ring around it.
4. **Check the empty state.** Restart without `VMS_FIXTURES=1`. With nothing published yet you should get the instruction copy, not an error, and a ruler with no bars — a measured, empty timeline.
5. **Confirm gaps aren't elements.** Inspect the track in devtools: you should find `div.tick`, `button.run`, and `div.playhead` — and nothing representing a gap.

If the runs render at plausible-but-wrong positions, don't debug the CSS. Re-run `node geo_test.mjs` first: the arithmetic is where that bug lives, and it takes two seconds to rule in or out.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Timeline is blank but `/api/fragments` returns runs in `curl` | Check the browser console — most often a JS error before `renderTimeline`, or `app.js` served from cache (Lesson 13's `Cache-Control`). |
| Overlay never hides no matter what the JS does | Missing `[hidden] { display: none !important; }` — the author `display: flex` is beating the UA stylesheet. Step 5. |
| Page scrolls sideways on a narrow window | An edge tick label hanging outside the track; apply Step 4's clamp. |
| Bars are in the right order but all shifted | `state.win` computed once at startup instead of at the top of each poll. |
| Labels read the right times but ticks don't line up under them | Ticks divided evenly across the track instead of placed on real clock minutes. Step 3. |
| A short recording appears in `/api/fragments` but never on screen | The `Math.max(…, 0.15)` width floor is missing; the bar is sub-pixel. |
| Clicking near the playhead does nothing | Missing `pointer-events: none` on `.playhead`. |
| Everything renders, but the timeline is off the bottom of the screen | The video stage has no height cap; give it `height: clamp(200px, 42vh, 420px)` so the signature element stays visible. |

## Recap

- The whole screen is one coordinate system: `pctOf` maps time to position, `timestampAt` maps position back to time, and they are exact inverses.
- `barGeometry` handles four real cases — inside, overflowing left, overflowing right, entirely outside — plus a minimum width so brief recordings can't render sub-pixel.
- Gaps are never elements; they are the track's background where no bar was drawn, which is why they cost no code and can never disagree with the runs.
- Ticks sit on real clock minutes, found with `Math.ceil(win.start / 60) * 60` — dividing the track evenly looks right and is wrong.
- Runs are `<button>`s, which buys focusability, keyboard activation, and semantics from the browser instead of imitating them.
- An empty archive is an instruction, not an error — it is what every first load looks like.
- `[hidden]` needs an explicit `display: none !important` rule wherever an author `display` would otherwise win.
- Polling recomputes the window each cycle (that's what makes it slide) and keeps the previous runs on a failed fetch rather than blanking the screen.

## Exercises

1. Set `TIMELINE_WINDOW_MINUTES = 15` in `app.js` (leave `.env` alone for a moment) and reload. The ruler gets four times denser and the labels crowd. Now decide: at 15 minutes, should labels still be quarter-hourly? Change the `% 15` in `tickMarks` to `% 5` and see which reads better — then put both values back and note in a comment why the tick rule and the window length are coupled.
2. Delete the `Math.max(0, …)` clamp from `barGeometry`, run the server with fixtures, and edit one fixture span to start 90 minutes ago. Watch the bar render outside the track. This is what unclamped geometry looks like — worth seeing once so you recognize it instantly later.
3. Add a fifth fixture run only 3 seconds long. Confirm it's visible as a hairline, then temporarily remove the `0.15` floor and confirm it vanishes entirely while still being present in the JSON.
4. Break the ticks deliberately: replace `tickMarks` with a version that places 60 evenly-spaced marks across the window (`win.start + i * 60` for `i` in 0..59). The timeline still looks completely normal. Now compare a label against the tick beneath it against your system clock — and note how long you'd have plausibly shipped this.
5. Turn on "Reduce motion" in your OS accessibility settings and reload. Confirm nothing about the timeline changes (there are no transitions to disable) — then explain in one sentence why a design with "motion on the playhead only" is nearly `prefers-reduced-motion`-correct by construction.

## Where this is going

The timeline renders real runs and real gaps, and it slides. Lesson 15 makes it *do* something: clicking a bar mints an HLS URL and plays that moment, the playhead tracks playback, and the Start/Stop button takes control of the edge agent — at which point every piece built since Lesson 1 is running at once, and the system is finished.
