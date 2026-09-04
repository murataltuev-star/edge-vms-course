# Lesson 10 — Deconstructing the Real Pipeline: Pacing, Looping, and `kvssink`

**Module:** GStreamer & Media Pipelines (Module 5)
**You will build:** `pipeline.py`, the function that constructs the real project's exact pipeline as an argv list, and understand the one problem that has nothing to do with GStreamer syntax at all.
**Time:** ~75–90 minutes.

## Why this lesson exists

Lesson 9 gave you every element the real project's pipeline needs, except two, and ran the harmless three-quarters of it — the part that never touches AWS. This lesson adds the last two elements, explains the actual hard problem they solve, and reconnects the result to `looper.py` from Lessons 5 and 8: the real pipeline is not a script that loops forever like `camera_sim.py` was. It's a process that legitimately ends, on purpose, every time — and your supervisor already knows exactly what to do about that.

## Prerequisites

- Lesson 9 completed — this lesson assumes you can read `element ! element` syntax and know what `filesrc`, `qtdemux`, and `h264parse` each do.
- Lessons 5 and 8 (`looper.py`, in both its plain and Docker-mode forms) — Step 3 of this lesson reconnects directly to a branch of that code you've already written but never actually exercised.

## Learning objectives

1. Read the real project's complete pipeline, element by element, including the two pieces Lesson 9 didn't cover.
2. Explain, and demonstrate, exactly why a file read from disk needs deliberate pacing before it can look like a live feed.
3. Explain why the real pipeline loops by restarting the whole process, not by looping internally — and recognize that Lesson 5's supervisor already handles this correctly, by design.
4. Build `pipeline.py`: the real pipeline expressed as an argv list, in the same style as every subprocess call since Lesson 5.
5. (Optional capstone) Understand what changes to actually publish to a real Kinesis Video Stream.

---

## Step 1 — The full pipeline

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

Three pieces are already familiar from Lesson 9 (`filesrc`, `qtdemux`, `h264parse`). Four things are new:

**`qtdemux name=d d.video_0`** — `name=d` labels this specific element instance `d`, so a later part of the pipeline description can refer to one of its pads by name: `d.video_0` means "the first video pad that `qtdemux` (named `d`) produces." Lesson 9's example didn't need this because a synthetic test clip has exactly one stream, and `gst-launch-1.0` can auto-link a demuxer with only one output pad without being told which one to use. A real-world MP4 often has *both* video and audio tracks — `qtdemux` would then expose multiple pads, and without naming one explicitly, GStreamer has no way to know which one the next element (`h264parse`, which only understands video) should receive.

**`video/x-h264,stream-format=avc,alignment=au`** — this line is not an element. It's a **caps filter**: instead of letting two elements negotiate a format automatically (Lesson 9, Step 3), you assert one explicitly. `kvssink` specifically requires `alignment=au` — complete access units (whole, individually-decodable frames), not arbitrary byte boundaries — and refuses anything looser. Writing the caps explicitly here, rather than hoping negotiation lands on the right thing, is the difference between "it happened to work" and "it's guaranteed to work."

**`identity sync=true`** — the entire subject of Step 2, next.

**`kvssink ...`** — the sink, and the only element in this whole pipeline that talks to AWS. `stream-name` and `aws-region` say what and where; `storage-size=128` is an internal buffering limit in megabytes (a local memory budget, not a KVS concept); `retention-period` matches the `.env`'s `KVS_RETENTION_HOURS` — how long AWS keeps the archived footage before discarding it. Everything to kvssink's left in this pipeline has now been run, fully locally, in Lesson 9. `kvssink` is where "local media processing" ends and "the actual cloud service" begins — one element, doing one job, at the very end of the chain.

## Step 2 — The pacing problem, demonstrated

Recall Lesson 9's Exercise 4: time how long remuxing a 10-second clip actually takes. It should have finished in a small fraction of a second — nowhere close to 10 seconds. `filesrc` reads exactly as fast as your disk allows, with no regard for the video's own declared duration.

This matters enormously for a *live* archive. Kinesis Video Streams expects media arriving with real, live-paced timestamps — a stream is supposed to represent *now*, continuously. If a 60-second clip is fed to `kvssink` in under a second, KVS receives 60 seconds of video compressed into well under one second of actual wall-clock arrival time. The archive doesn't get 60 seconds of timeline; it gets a fraction of one.

**`identity` is a pass-through element** — by itself, it changes nothing about the data flowing through it. Its `sync=true` property does exactly one thing: it throttles the pipeline to the stream's own declared timestamps, so that data is only released downstream at the pace a real-time viewer would actually experience it — precisely the job `ffmpeg`'s well-known `-re` flag ("read input at native frame rate") does for that tool.

You don't have a `kvssink` handy to watch this land in AWS, but you can watch the identical underlying mechanism directly, using a tool you already have. `ffmpeg`'s `-re` flag solves the exact same problem `identity sync=true` does — pacing playback to real time instead of disk speed — so timing it with and without `-re` shows you the real effect:

```bash
time ffmpeg -i clip.mp4 -f null -
time ffmpeg -re -i clip.mp4 -f null -
```

Run both against the 10-second `clip.mp4` from Lesson 9. The first should complete in well under a second — `ffmpeg` decoding as fast as your CPU allows, no pacing at all. The second should take *approximately 10 real seconds* — the same file, the same content, deliberately throttled to arrive at the rate a live viewer would actually see it. That's the entire pacing problem, and its fix, made visible with a stopwatch: one flag is the difference between "drained instantly" and "paced to reality." `identity sync=true` is GStreamer's version of exactly that flag, sitting in the pipeline for exactly this reason.

## Step 3 — Looping by restarting the process, not the pipeline

`camera_sim.py` never exited on its own — it looped internally, forever, until told to stop. The real pipeline is the opposite: `filesrc` reaches **end-of-stream (EOS)** the moment the clip finishes, and the whole `gst-launch-1.0` process exits cleanly, every time, on a finite test clip.

Two tempting fixes, and the one the real project actually uses:

- **Tempting fix: loop inside the same pipeline** — `multifilesrc`, or a manual seek-back-to-zero on EOS. Rejected, because doing this correctly means resetting the stream's timestamps backwards at the seam — and KVS requires timestamps that only ever increase. A naive in-pipeline loop either breaks that invariant or requires much more careful timestamp bookkeeping than restarting a whole new process does.
- **What the spec actually does: let the process end, and start a brand new one.** Each fresh launch of `gst-launch-1.0` begins at the *current* wallclock time, so timestamps stay monotonically increasing across the restart — the same guarantee a brand new recording session would have.

Here's the part worth sitting with: **you already built the supervisor for this, and you've never seen it actually take this path.** Go back to Lesson 5's (or Lesson 8's) `looper.py`:

```python
if returncode == 0:
    _log(f"loop {loop_num} exited cleanly after {duration:.1f}s, restarting")
    backoff = BACKOFF_START
    continue
```

`camera_sim.py` was an infinite loop — it never legitimately exited with `returncode == 0` unless you explicitly asked it to (Lesson 5's Exercise 2), so this branch mostly sat unused in your testing. The real pipeline exercises it *every single time the clip ends* — a clean EOS exit is the normal, expected, constant behavior, not a rare edge case. The seam between one launch ending and the next beginning becomes a real, visible gap in the archive's timeline — and the spec is explicit that this is desirable, not a flaw: it gives the timeline UI (from Module 1) something genuine to render, rather than a synthetic gap manufactured for demonstration purposes.

The seam's length depends on *how* the process is relaunched — directly measurable, and a concrete payoff from Module 4's work: on the host, restarting a plain process takes on the order of 200–400 milliseconds; in Docker mode (Lesson 8), each loop iteration starts an entirely new container, measured at roughly 2 seconds — a real, quantified cost of containerizing something you restart this frequently, not a rounding error.

## Step 4 — `pipeline.py`: the argv, as a list

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

Same discipline as `_build_argv()` in Lessons 5, 6, and 8: **a list of strings, never a shell string, never `shell=True`.** It matters even more here than it did for `camera_sim.py`, because `clip_path` is a real filename that could — depending on how it was chosen — contain characters a shell would interpret specially. Building the argv as literal Python list elements means nothing here is ever handed to `/bin/sh` for parsing; `gst-launch-1.0` receives each string as-is and parses its own pipeline-description mini-language directly, with no shell in between.

Wire this into Lesson 8's supervisor by replacing the child-selection logic: instead of `[sys.executable, CHILD_SCRIPT]`, host mode becomes `build_pipeline_argv(...)`, and Docker mode wraps that same list in `docker run` exactly as before. Nothing else in `looper.py` changes — not the signal handling, not the backoff, not the container-orphan cleanup. The supervisor was already generic enough not to care what it supervises; this is the proof.

## Step 5 — Optional capstone: actually publishing to a real stream

Everything above runs with zero AWS dependency, by design — matching the Docker module's decision to keep the heavy, real build optional rather than required. If you *do* have a working `kvssink` — either from Lesson 8's real Dockerfile build, or a native install — here's what actually changes to go from "the pipeline is correct" to "there is real footage in AWS":

- You need real credentials (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`) and the IAM policy the reference spec documents (`DescribeStream`, `CreateStream`, `GetDataEndpoint`, `PutMedia`, `ListFragments`, `GetHLSStreamingSessionURL`).
- You do **not** necessarily need to create the stream yourself first: AWS's own documentation for the producer SDK's GStreamer plugin states plainly that *"if a KVS stream with the provided or default name does not exist, the stream will automatically be created"* — provided your credentials include `CreateStream`. `kvssink` will provision it on first use.
- Run the pipeline from Step 4 with a real `stream-name` and region, let it run for a few minutes, then check the AWS Console (Kinesis Video Streams → your stream → check for media) — real footage, published by a pipeline you built and understand element by element.

This is also the natural place to stop if you don't have AWS credentials on hand yet, or haven't built the real `kvssink`. Everything in Module 6 — listing archived fragments, generating playback URLs — reads *from* a stream; it doesn't require you to have run this capstone, only to understand what the code would be querying if you had.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Timing the two `ffmpeg` commands in Step 2 shows little difference | Confirm you're timing the `time` command around the whole `ffmpeg` invocation, not something else — and that `clip.mp4` genuinely has the ~10 second duration from Lesson 9's generation command. |
| `no element "kvssink"` | Expected if you skipped Step 5 — this is Lesson 9's own troubleshooting entry, now appearing for a real reason: the Producer SDK isn't built, or `GST_PLUGIN_PATH` isn't set to point at it (Lesson 8, Step 6). |
| Pipeline exits immediately with no gap between loops when you expected one | Check you're actually restarting the whole process (a fresh `gst-launch-1.0` invocation) per loop, not trying to loop the source internally — the gap *is* the restart overhead, and it should be small but nonzero. |
| Timestamps look wrong / archive gaps seem negative or overlapping | Almost always a symptom of trying the rejected "loop inside one pipeline" approach from Step 3 — restart the whole process instead. |
| `kvssink` publishes but the AWS Console shows no data after several minutes | Check the stream name and region actually match what you configured, and that your IAM policy includes every action listed in Step 5 — a missing `PutMedia` permission fails silently from the pipeline's perspective in some SDK versions. |

## Recap

- `qtdemux name=d d.video_0` names an element so a specific pad can be referenced explicitly, needed once a demuxer can produce more than one kind of output pad.
- A caps filter (`video/x-h264,stream-format=avc,alignment=au`) asserts an exact format between two elements instead of relying on automatic negotiation — required here because `kvssink` needs whole access units specifically.
- `identity sync=true` paces a pipeline to the media's own real-time duration — the identical problem `ffmpeg -re` solves, demonstrated with a stopwatch rather than taken on faith.
- The real pipeline loops by letting the whole process exit at EOS and relaunching a fresh one, keeping timestamps monotonically increasing — and Lesson 5/8's supervisor already handles this correctly, in a branch you likely hadn't exercised until now.
- The restart seam is a real, desirable gap in the timeline, not a flaw — and measurably longer in Docker mode than on the host, a concrete cost of containerizing something restarted this often.
- `pipeline.py` builds the real pipeline as an argv list, for the same shell-injection reasons as every other subprocess call since Lesson 5.
- `kvssink` can create its own target stream automatically if it doesn't already exist, given the right IAM permissions — no separate provisioning step is strictly required before the very first run.

## Exercises

1. Generate a 3-second clip (`ffmpeg -f lavfi -i testsrc=duration=3:size=640x480:rate=30 ...`) and run it through Lesson 5's `looper.py` restart loop (swap in `build_pipeline_argv`, pointed at `filesink` instead of `kvssink` for now) for five iterations. Log and compare the actual gap between "loop N stopped" and "loop N+1 started" — does it match the ~200–400ms host-mode figure this lesson cites?
2. Deliberately omit `sync=true` from `identity` (leave the element in, just drop the property) and re-run Step 2's timing experiment's GStreamer equivalent conceptually — predict, before checking `gst-inspect-1.0 identity`, what `sync`'s default value is and whether omitting it changes the pacing behavior at all.
3. Using `gst-inspect-1.0 kvssink` (once you have it, from the optional capstone), find `storage-size`'s default value and explain, in a sentence, what would happen to a very long recording if this buffer filled up faster than `kvssink` could upload to AWS.
4. The real spec requires `alignment=au` explicitly rather than letting negotiation pick a value. Using what Lesson 9 taught about caps mismatches, explain what kind of error you'd expect to see if `h264parse`'s actual output couldn't satisfy that explicit assertion.

## Where this is going

Whether or not you ran the optional capstone, the next module assumes a stream *could* have real footage in it. Module 6 covers `boto3`: how the FastAPI backend actually queries Kinesis Video Streams — listing what fragments of footage exist (`GET /api/fragments`) and minting a short-lived playback URL for any moment in the archive (`GET /api/hls`) — the two endpoints that turn a stream of published video into something a browser can actually browse.
