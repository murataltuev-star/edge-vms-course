# Lesson 4 — GStreamer — Fundamentals, and the Real Pipeline Deconstructed

**Module:** KVS-VMS — a cloud VMS on Kinesis Video Streams (Module 8)
**You will build:** several working GStreamer pipelines that run entirely on your own machine; then `pipeline.py`, the function that builds the real project's exact pipeline as an argv list — and the pacing problem that has nothing to do with video.
**Time:** ~2–2.5 hours, in two parts.

> **This lesson is in 2 parts** — formerly Lessons 9–10 — and the step numbers run through all of them. Each part ends with its own troubleshooting table, recap and exercises; do the parts in order.

## Prerequisites

**Part A.**
- No specific lesson is a hard prerequisite for the GStreamer concepts themselves, but this lesson's last section assumes you remember `camera_sim.py`'s job (Lesson 2) and the argv-as-a-list discipline (Lessons 2 and 3).
- GStreamer installed:
  - **macOS:** `brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly`
  - **Debian/Ubuntu:** `sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav`
  - Verify with `gst-launch-1.0 --version`.
- `ffmpeg` installed, to generate a test clip in Step 4 (`ffmpeg -version` to check).

**Part B.**
- Part A completed — this lesson assumes you can read `element ! element` syntax and know what `filesrc`, `qtdemux`, and `h264parse` each do.
- Lessons 2 and 3 (`looper.py`, in both its plain and Docker-mode forms) — Step 3 of this lesson reconnects directly to a branch of that code you've already written but never actually exercised.

## Learning objectives

1. Explain what a GStreamer pipeline is: a linked chain of elements, each with one job, connected by pads.
2. Read and write `gst-launch-1.0`'s `element ! element ! element` syntax.
3. Distinguish source, filter, and sink elements, and explain what caps negotiation means.
4. Use `-v` to see negotiated caps and `gst-inspect-1.0` to look up any element's properties, the way you'd read a function's signature.
5. Build a pipeline that demuxes and re-parses a real H.264 file, entirely on your own machine.
6. Read the real project's complete pipeline, element by element, including the two pieces Part A didn't cover.
7. Explain, and demonstrate, exactly why a file read from disk needs deliberate pacing before it can look like a live feed.
8. Explain why the real pipeline loops by restarting the whole process, not by looping internally — and recognize that Lesson 2's supervisor already handles this correctly, by design.
9. Build `pipeline.py`: the real pipeline expressed as an argv list, in the same style as every subprocess call since Lesson 2.
10. (Optional capstone) Understand what changes to actually publish to a real Kinesis Video Stream.

---

## Part A — GStreamer Fundamentals: Pipelines, Elements, and `gst-launch-1.0`


Since Module 2, the edge agent's job has been "supervise a process." `camera_sim.py` was always a stand-in for something specific: a real multimedia pipeline that reads a video file and prepares it to be published to AWS. GStreamer is the framework the real project uses to build that pipeline, and `gst-launch-1.0` is the command-line tool for describing one without writing any code at all. This lesson teaches the framework itself, with every example running locally — the same "stand-in first" discipline this whole course has followed, just at a different layer: instead of avoiding AWS with a dummy Python script, you avoid it here by pointing pipelines at your screen and local disk instead of at `kvssink`.
## Step 1 — The simplest possible pipeline

```bash
gst-launch-1.0 videotestsrc ! autovideosink
```

A window should open showing a moving test pattern (color bars or a similar synthetic image). `Ctrl+C` to stop — that's `SIGINT`, the exact signal from Lesson 2, now stopping a GStreamer process instead of a Python one.

Read the two names either side of `!`:

- `videotestsrc` is a **source** element: it produces data and has no input. It doesn't read a file — it generates a synthetic test pattern out of nothing, which makes it perfect for experimenting without needing any media file at all.
- `autovideosink` is a **sink** element: it consumes data and has no output. It picks whatever video output actually works on your OS (an X11 window, a macOS window, and so on) so you don't have to know the exact right sink for your platform.
- `!` is **not a shell pipe**. There is no shell parsing this at all beyond splitting words — `!` is `gst-launch-1.0`'s own pipeline-description syntax, meaning "connect the output of the element on the left to the input of the element on the right." You'll see this exact distinction matter again in Part B, the same way `argv` being a list rather than a shell string mattered in Lessons 2 and 3.

## Step 2 — Three kinds of elements

Add a filter in the middle:

```bash
gst-launch-1.0 videotestsrc ! videoflip method=clockwise ! autovideosink
```

The picture now rotates 90°. `videoflip` is the third category:

- **Source** — no input, one output. Produces data. (`videotestsrc`, and later `filesrc`.)
- **Filter / transform** — one input, one output. Changes data as it passes through, without adding or removing anything structural. (`videoflip` here; `h264parse` later.)
- **Sink** — one input, no output. Consumes data — displays it, writes it to a file, or (later) uploads it. (`autovideosink`; later `filesink`, and eventually `kvssink`.)

A `gst-launch-1.0` pipeline, in the form you'll use throughout this course, is a straight-line chain: exactly one source, any number of filters, exactly one sink. (GStreamer supports branching pipelines with elements like `tee`, but the real project's pipeline — and everything in this course — is a straight line, so that's all you need.)

`method=clockwise` is a **property** — a configurable value specific to that one element. Properties are set directly after the element's name, `key=value`, with no extra punctuation. You'll set several on `kvssink` in Part B.

## Step 3 — Pads and caps: what's actually being negotiated

Every `!` connection is really a **pad**-to-pad link — an element's named connection point. Before any data flows, the two elements negotiate **caps** ("capabilities"): the exact format they'll exchange — resolution, framerate, color layout, codec, and so on. Most of the time this happens automatically and invisibly. See it happen with `-v`:

```bash
gst-launch-1.0 -v videotestsrc ! autovideosink
```

Among the verbose output, look for a line shaped like:

```
/GstPipeline:pipeline0/GstVideoTestSrc:videotestsrc0.GstPad:src: caps = video/x-raw, format=(string)xxx, width=(int)320, height=(int)240, framerate=(fraction)30/1
```

That's the caps actually agreed on for that specific pad — width, height, framerate, pixel format, all negotiated without you specifying any of it. Keep this in mind for Part B: the single most common reason a GStreamer pipeline refuses to start at all is a caps mismatch between two elements that can't agree on a format — and `-v` is the first thing you reach for to see exactly where that disagreement is.

## Step 4 — `gst-inspect-1.0`: an element's own documentation

```bash
gst-inspect-1.0 videotestsrc
```

Read through the output's shape, not every line:

- **Pad Templates** — what each pad (`SRC`, `SINK`) is capable of producing or accepting.
- **Element Properties** — every configurable knob, each with its type, default value, and a description.

This is the same relationship a Python function's signature and docstring have to the function itself — one command tells you everything an element can do, without needing to already know it. This is exactly how you'll look up `kvssink`'s own properties in Part B (`stream-name`, `aws-region`, `retention-period`, and others) rather than needing to memorize them from documentation.

Try `gst-inspect-1.0 kvssink` right now, even though it isn't installed yet — you should get `No such element or plugin 'kvssink'`. Keep that exact message in mind: it's the first line of the real project's own known-failure-modes table, and you've now seen it appear for a genuine reason (the element really isn't installed) rather than reading about it secondhand.

## Step 5 — A real file, demuxed and re-parsed, entirely locally

Generate a short, real H.264 test clip — no camera or downloaded file needed:

```bash
ffmpeg -f lavfi -i testsrc=duration=10:size=640x480:rate=30 \
  -c:v libx264 -an -g 30 -pix_fmt yuv420p clip.mp4
```

This is the exact normalization command the real project's own README gives for turning any source video into what its pipeline expects: H.264 video, no audio (`-an`), a short GOP (`-g 30` — keyframes at least every 30 frames, which keeps fragments small and seeking fine-grained), and 4:2:0 pixel format (`-pix_fmt yuv420p`, the most broadly compatible choice).

Now run the first three elements of the real project's actual pipeline — the ones that need nothing from AWS at all:

```bash
gst-launch-1.0 filesrc location=clip.mp4 ! qtdemux ! h264parse ! filesink location=remuxed.h264
```

Element by element:

- `filesrc location=clip.mp4` — a source that reads bytes from a file on disk, as fast as the disk allows (hold onto that phrase — it's the entire subject of Part B's next section).
- `qtdemux` — a **demultiplexer**. An MP4 file is a *container format*: a wrapper holding one or more encoded streams (video, sometimes audio) plus timing metadata. `qtdemux` unwraps that container and extracts the raw H.264 elementary stream from inside it.
- `h264parse` — normalizes the extracted H.264 bytestream into properly-delimited access units (complete, individually-decodable frames) rather than an arbitrary sequence of bytes. You'll see exactly why this specific guarantee matters to `kvssink` in Part B.
- `filesink location=remuxed.h264` — writes whatever arrives at this element straight to a file. No AWS, no network, nothing beyond your own disk.

Confirm it worked:

```bash
ls -la remuxed.h264
gst-launch-1.0 filesrc location=remuxed.h264 ! h264parse ! avdec_h264 ! autovideosink
```

The second command decodes and displays the re-muxed file — if you see the same moving test pattern `clip.mp4` was generated from, the extracted elementary stream is genuinely valid, playable H.264, produced by the identical three elements (`filesrc`, `qtdemux`, `h264parse`) the real pipeline uses before it ever touches AWS.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `no element "qtdemux"` or `no element "h264parse"` | Missing plugin package — install the `gst-plugins-good`/`gst-plugins-bad` sets for your platform (Step 1's install commands). |
| Pipeline fails immediately with a caps-related error | Two adjacent elements couldn't agree on a format — add `-v` and look for the last caps line printed before the failure. |
| `gst-launch-1.0 videotestsrc ! autovideosink` never stops on its own | Expected — `videotestsrc` is a synthetic source with no natural end; only `Ctrl+C` (or an explicit `num-buffers=N` property, Exercise 1) stops it. Contrast with `filesrc`, which reaches end-of-stream when the file does. |
| `ffmpeg` command fails to find `libx264` | Your `ffmpeg` build lacks the x264 encoder — install a build that includes it (most package-manager builds do by default). |
| Decoded playback (`avdec_h264 ! autovideosink`) shows a black or blank window | Confirm `remuxed.h264` is non-empty (`ls -la`) — an empty file usually means the first pipeline exited before `qtdemux` found a video pad, often because the input wasn't actually H.264 in MP4. |

### Recap

- A pipeline is a linked chain of elements — source, then any number of filters, then sink — connected pad to pad.
- `!` in a `gst-launch-1.0` command line is pipeline-description syntax, not a shell pipe; nothing about it involves `/bin/sh`.
- Two connected elements negotiate **caps** (the exact format they'll exchange) before data flows; `-v` shows you what was actually agreed.
- `gst-inspect-1.0 <element>` is that element's own reference documentation — pad templates and properties, discoverable without prior knowledge.
- `qtdemux` unwraps a container format (MP4) to expose the raw encoded stream inside it; `h264parse` normalizes that stream into clean access units.
- Everything up through `h264parse` in the real project's pipeline is pure local media processing — testable, as you just did, with zero AWS dependency.

### Exercises

1. Add `num-buffers=300` as a property on `videotestsrc` (`gst-launch-1.0 videotestsrc num-buffers=300 ! autovideosink`) and confirm the pipeline now exits on its own instead of running until `Ctrl+C` — explain in one sentence why this makes `videotestsrc` behave more like `filesrc` for testing purposes.
2. Run `gst-inspect-1.0 h264parse` and find the property that controls whether output is delimited by access units versus NAL units — you'll meet the caps filter that pins this down explicitly in Part B.
3. Deliberately break the second pipeline in Step 5 by pointing `filesrc` at a `.mp3` or plain text file instead of `clip.mp4`, and read the actual error `qtdemux` produces — this is what a "wrong container format" failure looks like firsthand, rather than as a hypothetical.
4. Time how long the remux pipeline in Step 5 takes to run (`time gst-launch-1.0 filesrc location=clip.mp4 ! qtdemux ! h264parse ! filesink location=remuxed.h264`) against a 10-second clip. Hold onto that number — Part B opens by asking you to explain why it's nowhere near 10 seconds.

### Where this is going

Part B takes the real project's full pipeline — the same `filesrc`/`qtdemux`/`h264parse` you just ran, plus two new elements (`identity sync=true` and `kvssink`) — and explains the one problem none of this lesson's examples had to deal with: making a file that reads instantly look, to AWS, like a camera streaming in real time.

---

## Part B — Deconstructing the Real Pipeline: Pacing, Looping, and `kvssink`


Part A gave you every element the real project's pipeline needs, except two, and ran the harmless three-quarters of it — the part that never touches AWS. This lesson adds the last two elements, explains the actual hard problem they solve, and reconnects the result to `looper.py` from Lessons 2 and 3: the real pipeline is not a script that loops forever like `camera_sim.py` was. It's a process that legitimately ends, on purpose, every time — and your supervisor already knows exactly what to do about that.
## Step 6 — The full pipeline

This is the real project's pipeline, in full, from the reference spec:

```bash
gst-launch-1.0 -q \
  filesrc location=$CLIP_PATH ! \
  qtdemux name=d d.video_0 ! \
  h264parse ! \
  video/x-h264,stream-format=avc,alignment=au ! \
  identity sync=true ! \
  kvssink stream-name=$KVS_STREAM_NAME \
          aws-region=$AWS_REGION \
          storage-size=128 \
          retention-period=$KVS_RETENTION_HOURS
```

Three pieces are already familiar from Part A (`filesrc`, `qtdemux`, `h264parse`). Four things are new:

**`qtdemux name=d d.video_0`** — `name=d` labels this specific element instance `d`, so a later part of the pipeline description can refer to one of its pads by name: `d.video_0` means "the first video pad that `qtdemux` (named `d`) produces." Part A's example didn't need this because a synthetic test clip has exactly one stream, and `gst-launch-1.0` can auto-link a demuxer with only one output pad without being told which one to use. A real-world MP4 often has *both* video and audio tracks — `qtdemux` would then expose multiple pads, and without naming one explicitly, GStreamer has no way to know which one the next element (`h264parse`, which only understands video) should receive.

**`video/x-h264,stream-format=avc,alignment=au`** — this line is not an element. It's a **caps filter**: instead of letting two elements negotiate a format automatically (Part A, Step 8), you assert one explicitly. `kvssink` specifically requires `alignment=au` — complete access units (whole, individually-decodable frames), not arbitrary byte boundaries — and refuses anything looser. Writing the caps explicitly here, rather than hoping negotiation lands on the right thing, is the difference between "it happened to work" and "it's guaranteed to work."

**`identity sync=true`** — the entire subject of Step 7, next.

**`kvssink ...`** — the sink, and the only element in this whole pipeline that talks to AWS. `stream-name` and `aws-region` say what and where; `storage-size=128` is an internal buffering limit in megabytes (a local memory budget, not a KVS concept); `retention-period` matches the `.env`'s `KVS_RETENTION_HOURS` — how long AWS keeps the archived footage before discarding it. Everything to kvssink's left in this pipeline has now been run, fully locally, in Part A. `kvssink` is where "local media processing" ends and "the actual cloud service" begins — one element, doing one job, at the very end of the chain.

## Step 7 — The pacing problem, demonstrated

Recall Part A's Exercise 4: time how long remuxing a 10-second clip actually takes. It should have finished in a small fraction of a second — nowhere close to 10 seconds. `filesrc` reads exactly as fast as your disk allows, with no regard for the video's own declared duration.

This matters enormously for a *live* archive. Kinesis Video Streams expects media arriving with real, live-paced timestamps — a stream is supposed to represent *now*, continuously. If a 60-second clip is fed to `kvssink` in under a second, KVS receives 60 seconds of video compressed into well under one second of actual wall-clock arrival time. The archive doesn't get 60 seconds of timeline; it gets a fraction of one.

**`identity` is a pass-through element** — by itself, it changes nothing about the data flowing through it. Its `sync=true` property does exactly one thing: it throttles the pipeline to the stream's own declared timestamps, so that data is only released downstream at the pace a real-time viewer would actually experience it — precisely the job `ffmpeg`'s well-known `-re` flag ("read input at native frame rate") does for that tool.

You don't have a `kvssink` handy to watch this land in AWS, but you can watch the identical underlying mechanism directly, using a tool you already have. `ffmpeg`'s `-re` flag solves the exact same problem `identity sync=true` does — pacing playback to real time instead of disk speed — so timing it with and without `-re` shows you the real effect:

```bash
time ffmpeg -i clip.mp4 -f null -
time ffmpeg -re -i clip.mp4 -f null -
```

Run both against the 10-second `clip.mp4` from Part A. The first should complete in well under a second — `ffmpeg` decoding as fast as your CPU allows, no pacing at all. The second should take *approximately 10 real seconds* — the same file, the same content, deliberately throttled to arrive at the rate a live viewer would actually see it. That's the entire pacing problem, and its fix, made visible with a stopwatch: one flag is the difference between "drained instantly" and "paced to reality." `identity sync=true` is GStreamer's version of exactly that flag, sitting in the pipeline for exactly this reason.

## Step 8 — Looping by restarting the process, not the pipeline

`camera_sim.py` never exited on its own — it looped internally, forever, until told to stop. The real pipeline is the opposite: `filesrc` reaches **end-of-stream (EOS)** the moment the clip finishes, and the whole `gst-launch-1.0` process exits cleanly, every time, on a finite test clip.

Two tempting fixes, and the one the real project actually uses:

- **Tempting fix: loop inside the same pipeline** — `multifilesrc`, or a manual seek-back-to-zero on EOS. Rejected, because doing this correctly means resetting the stream's timestamps backwards at the seam — and KVS requires timestamps that only ever increase. A naive in-pipeline loop either breaks that invariant or requires much more careful timestamp bookkeeping than restarting a whole new process does.
- **What the spec actually does: let the process end, and start a brand new one.** Each fresh launch of `gst-launch-1.0` begins at the *current* wallclock time, so timestamps stay monotonically increasing across the restart — the same guarantee a brand new recording session would have.

Here's the part worth sitting with: **you already built the supervisor for this, and you've never seen it actually take this path.** Go back to Lesson 2's (or Lesson 3's) `looper.py`:

```python
if returncode == 0:
    _log(f"loop {loop_num} exited cleanly after {duration:.1f}s, restarting")
    backoff = BACKOFF_START
    continue
```

`camera_sim.py` was an infinite loop — it never legitimately exited with `returncode == 0` unless you explicitly asked it to (Lesson 2's Exercise 2), so this branch mostly sat unused in your testing. The real pipeline exercises it *every single time the clip ends* — a clean EOS exit is the normal, expected, constant behavior, not a rare edge case. The seam between one launch ending and the next beginning becomes a real, visible gap in the archive's timeline — and the spec is explicit that this is desirable, not a flaw: it gives the timeline UI (from Module 1) something genuine to render, rather than a synthetic gap manufactured for demonstration purposes.

The seam's length depends on *how* the process is relaunched — directly measurable, and a concrete payoff from Module 4's work: on the host, restarting a plain process takes on the order of 200–400 milliseconds; in Docker mode (Lesson 3), each loop iteration starts an entirely new container, measured at roughly 2 seconds — a real, quantified cost of containerizing something you restart this frequently, not a rounding error.

## Step 9 — `pipeline.py`: the argv, as a list

```python
def build_pipeline_argv(clip_path, stream_name, aws_region, retention_hours):
    return [
        "gst-launch-1.0", "-q",
        "filesrc", f"location={clip_path}",
        "!", "qtdemux", "name=d", "d.video_0",
        "!", "h264parse",
        "!", "video/x-h264,stream-format=avc,alignment=au",
        "!", "identity", "sync=true",
        "!", "kvssink",
        f"stream-name={stream_name}",
        f"aws-region={aws_region}",
        "storage-size=128",
        f"retention-period={retention_hours}",
    ]
```

Same discipline as `_build_argv()` in Lessons 2 and 3: **a list of strings, never a shell string, never `shell=True`.** It matters even more here than it did for `camera_sim.py`, because `clip_path` is a real filename that could — depending on how it was chosen — contain characters a shell would interpret specially. Building the argv as literal Python list elements means nothing here is ever handed to `/bin/sh` for parsing; `gst-launch-1.0` receives each string as-is and parses its own pipeline-description mini-language directly, with no shell in between.

Wire this into Lesson 3's supervisor by replacing the child-selection logic: instead of `[sys.executable, CHILD_SCRIPT]`, host mode becomes `build_pipeline_argv(...)`, and Docker mode wraps that same list in `docker run` exactly as before. Nothing else in `looper.py` changes — not the signal handling, not the backoff, not the container-orphan cleanup. The supervisor was already generic enough not to care what it supervises; this is the proof.

## Step 10 — Optional capstone: actually publishing to a real stream

Everything above runs with zero AWS dependency, by design — matching the Docker module's decision to keep the heavy, real build optional rather than required. If you *do* have a working `kvssink` — either from Lesson 3's real Dockerfile build, or a native install — here's what actually changes to go from "the pipeline is correct" to "there is real footage in AWS":

- You need real credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) and the IAM policy the reference spec documents (`DescribeStream`, `CreateStream`, `GetDataEndpoint`, `PutMedia`, `ListFragments`, `GetHLSStreamingSessionURL`).
- You do **not** necessarily need to create the stream yourself first: AWS's own documentation for the producer SDK's GStreamer plugin states plainly that *"if a KVS stream with the provided or default name does not exist, the stream will automatically be created"* — provided your credentials include `CreateStream`. `kvssink` will provision it on first use.
- Run the pipeline from Step 9 with a real `stream-name` and region, let it run for a few minutes, then check the AWS Console (Kinesis Video Streams → your stream → check for media) — real footage, published by a pipeline you built and understand element by element.

This is also the natural place to stop if you don't have AWS credentials on hand yet, or haven't built the real `kvssink`. Everything in Module 6 — listing archived fragments, generating playback URLs — reads *from* a stream; it doesn't require you to have run this capstone, only to understand what the code would be querying if you had.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Timing the two `ffmpeg` commands in Step 7 shows little difference | Confirm you're timing the `time` command around the whole `ffmpeg` invocation, not something else — and that `clip.mp4` genuinely has the ~10 second duration from Part A's generation command. |
| `no element "kvssink"` | Expected if you skipped Step 10 — this is Part A's own troubleshooting entry, now appearing for a real reason: the Producer SDK isn't built, or `GST_PLUGIN_PATH` isn't set to point at it (Lesson 3, Step 15). |
| Pipeline exits immediately with no gap between loops when you expected one | Check you're actually restarting the whole process (a fresh `gst-launch-1.0` invocation) per loop, not trying to loop the source internally — the gap *is* the restart overhead, and it should be small but nonzero. |
| Timestamps look wrong / archive gaps seem negative or overlapping | Almost always a symptom of trying the rejected "loop inside one pipeline" approach from Step 8 — restart the whole process instead. |
| `kvssink` publishes but the AWS Console shows no data after several minutes | Check the stream name and region actually match what you configured, and that your IAM policy includes every action listed in Step 10 — a missing `PutMedia` permission fails silently from the pipeline's perspective in some SDK versions. |

### Recap

- `qtdemux name=d d.video_0` names an element so a specific pad can be referenced explicitly, needed once a demuxer can produce more than one kind of output pad.
- A caps filter (`video/x-h264,stream-format=avc,alignment=au`) asserts an exact format between two elements instead of relying on automatic negotiation — required here because `kvssink` needs whole access units specifically.
- `identity sync=true` paces a pipeline to the media's own real-time duration — the identical problem `ffmpeg -re` solves, demonstrated with a stopwatch rather than taken on faith.
- The real pipeline loops by letting the whole process exit at EOS and relaunching a fresh one, keeping timestamps monotonically increasing — and Lesson 2/8's supervisor already handles this correctly, in a branch you likely hadn't exercised until now.
- The restart seam is a real, desirable gap in the timeline, not a flaw — and measurably longer in Docker mode than on the host, a concrete cost of containerizing something restarted this often.
- `pipeline.py` builds the real pipeline as an argv list, for the same shell-injection reasons as every other subprocess call since Lesson 2.
- `kvssink` can create its own target stream automatically if it doesn't already exist, given the right IAM permissions — no separate provisioning step is strictly required before the very first run.

### Exercises

1. Generate a 3-second clip (`ffmpeg -f lavfi -i testsrc=duration=3:size=640x480:rate=30 ...`) and run it through Lesson 2's `looper.py` restart loop (swap in `build_pipeline_argv`, pointed at `filesink` instead of `kvssink` for now) for five iterations. Log and compare the actual gap between "loop N stopped" and "loop N+1 started" — does it match the ~200–400ms host-mode figure this lesson cites?
2. Deliberately omit `sync=true` from `identity` (leave the element in, just drop the property) and re-run Step 7's timing experiment's GStreamer equivalent conceptually — predict, before checking `gst-inspect-1.0 identity`, what `sync`'s default value is and whether omitting it changes the pacing behavior at all.
3. Using `gst-inspect-1.0 kvssink` (once you have it, from the optional capstone), find `storage-size`'s default value and explain, in a sentence, what would happen to a very long recording if this buffer filled up faster than `kvssink` could upload to AWS.
4. The real spec requires `alignment=au` explicitly rather than letting negotiation pick a value. Using what Part A taught about caps mismatches, explain what kind of error you'd expect to see if `h264parse`'s actual output couldn't satisfy that explicit assertion.

## Where this is going

Whether or not you ran the optional capstone, the next lesson assumes a stream *could* have real footage in it. Module 6 covers `boto3`: how the FastAPI backend actually queries Kinesis Video Streams — listing what fragments of footage exist (`GET /api/fragments`) and minting a short-lived playback URL for any moment in the archive (`GET /api/hls`) — the two endpoints that turn a stream of published video into something a browser can actually browse.
