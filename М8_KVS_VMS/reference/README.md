# Reference implementation — Lessons 14 & 15

`web/index.html`, `web/style.css` and `web/app.js` here are the exact files the two
frontend lessons build, in their finished state. They are provided so you can compare
against a known-good version, not as something to copy in place of building it — the
lessons' value is in the order the pieces arrive and the reasons given for each.

These files were verified by rendering them in a real browser (headless Chromium)
against a stub server, across five states: footage present, empty archive, playback
with a visible playhead, an externally-started agent, and a 380px viewport. The
geometry and interaction logic were additionally tested in `node` — 17 and 25
assertions respectively, reproduced verbatim in Lessons 14 and 15.

`timeline-rendered.png` is one of those renders: four runs, three gaps, the tick
ruler, and the meta row, at 1240px.

Not included: `server/` and `edge/`, which the lessons build incrementally and which
are listed in full in Lessons 5–13.
