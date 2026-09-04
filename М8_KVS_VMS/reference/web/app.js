// Duplicated from .env — there is no config endpoint. If you change one, change both.
const TIMELINE_WINDOW_MINUTES = 60;
const PLAYBACK_CHUNK_SECONDS = 300;
const POLL_MS = 10000;

// ---------------------------------------------------------------------------
// Pure geometry. No DOM, no fetch — everything here is a function of numbers,
// which is exactly why it is the part worth testing on its own.
// ---------------------------------------------------------------------------

export function windowFor(nowSec, minutes = TIMELINE_WINDOW_MINUTES) {
  return { start: nowSec - minutes * 60, end: nowSec };
}

export function pctOf(ts, win) {
  return ((ts - win.start) / (win.end - win.start)) * 100;
}

export function barGeometry(run, win) {
  const left = Math.max(0, pctOf(run.start, win));
  const right = Math.min(100, pctOf(run.end, win));
  if (right <= 0 || left >= 100) return null;      // entirely outside the window
  return { left, width: Math.max(right - left, 0.15) };  // floor so a 2s run stays visible
}

export function timestampAt(fraction, win) {
  return win.start + fraction * (win.end - win.start);
}

export function runAt(ts, runs) {
  return runs.find((r) => ts >= r.start && ts <= r.end) || null;
}

export function chunkFor(ts, run, chunkSeconds = PLAYBACK_CHUNK_SECONDS) {
  const end = Math.min(ts + chunkSeconds, run.end);
  let start = ts;
  if (end - start < 1) start = Math.max(run.start, end - 1);
  if (end - start <= 0) return null;               // run too short to play
  return { start, end };
}

export function tickMarks(win) {
  const marks = [];
  const first = Math.ceil(win.start / 60) * 60;
  for (let ts = first; ts <= win.end; ts += 60) {
    const minute = new Date(ts * 1000).getMinutes();
    marks.push({ ts, major: minute % 5 === 0, labeled: minute % 15 === 0 });
  }
  return marks;
}

export function fmtClock(ts) {
  const d = new Date(ts * 1000);
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}

export function totalCoverage(runs) {
  return runs.reduce((sum, r) => sum + (r.end - r.start), 0);
}

// ---------------------------------------------------------------------------
// Wiring. Everything below touches the DOM or the network.
// ---------------------------------------------------------------------------

const el = (id) => document.getElementById(id);

const state = {
  win: windowFor(Date.now() / 1000),
  runs: [],
  chunk: null,
  hls: null,
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

function showOverlay(html) {
  const o = el("overlay");
  o.innerHTML = html;
  o.hidden = false;
}
function hideOverlay() { el("overlay").hidden = true; }

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

  await pollRecording();
}

async function pollRecording() {
  try {
    const r = await fetch("/api/recording");
    applyRecordingState(await r.json());
  } catch (err) {
    console.error("recording poll failed", err);
  }
}

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
      showOverlay(
        "<span><strong>Playback failed for this segment.</strong>Pick another moment, or try again.</span>"
      );
      return;
    }
    state.chunk = chunk;
    loadHls(body.url);
    hideOverlay();
  } catch (err) {
    console.error("hls request failed", err);
    showOverlay("<span><strong>Playback failed for this segment.</strong>Pick another moment, or try again.</span>");
  } finally {
    state.inFlight = false;
  }
}

function loadHls(url) {
  const video = el("video");
  if (window.Hls && window.Hls.isSupported()) {
    if (state.hls) state.hls.destroy();
    state.hls = new window.Hls();
    state.hls.on(window.Hls.Events.ERROR, (_e, data) => {
      if (!data.fatal) return;
      console.error("hls.js fatal error", data);
      showOverlay("<span><strong>Playback failed for this segment.</strong>Pick another moment, or try again.</span>");
    });
    state.hls.loadSource(url);
    state.hls.attachMedia(video);
    video.play().catch(() => {});
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url;               // Safari plays HLS natively
    video.play().catch(() => {});
  } else {
    showOverlay("<span><strong>Playback failed for this segment.</strong>This browser cannot play HLS.</span>");
  }
}

function movePlayhead() {
  const playhead = el("playhead");
  if (!state.chunk) { playhead.hidden = true; return; }
  const at = state.chunk.start + el("video").currentTime;
  const pct = pctOf(at, state.win);
  playhead.hidden = pct < 0 || pct > 100;
  playhead.style.left = pct + "%";
}

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

if (typeof document !== "undefined") init();
