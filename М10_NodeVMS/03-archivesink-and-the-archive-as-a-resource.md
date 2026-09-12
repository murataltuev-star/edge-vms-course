# Lesson 3 — `archivesink`, and the Archive as a Resource

**Module:** NodeVMS — the platform's shape on one Node (Module 10)
**You will build:** a sink element that writes segments into the spool and promotes them into an archive resource with the epoch in every path and a manifest beside the footage — then kill it mid-segment, rebuild the manifest from the files, and apply retention as a policy.
**Time:** ~150 minutes.

## Why this lesson exists

М8 sent footage to Kinesis; М9 gave the box a spool and taught the rule that made the spool safe — *a segment is deleted on acknowledgement, never on send*. The archive in this module is that rule with the acknowledgement pointed at ourselves. KVS is gone. The index table from М9 Lesson 5 is gone too, and not because it was wrong: it was on the wrong side of a boundary. An index that lives in a database on the same box as the footage is an index that does not travel with the footage, has to be rebuilt by scanning, and needs a database. An index that lives *beside* the footage — a manifest per camera on the same disks — returns with the disks, needs no scan, and needs no database.

That is what makes the archive a **resource**: server-bound, with no controller, and with a policy. The worker writes into it; nothing moves it; when its server dies its footage is unavailable and everything else carries on.

> **What you can verify without hardware.** Everything except the element: `tests/test_lesson3_archive.py` runs the acknowledgement order, the kill-at-minute-seven accounting, the manifest rebuilt from files alone, the fenced epoch on a timeline, two resources merged, and retention — against real files in a temp directory, no GStreamer. `gstvms/archivesink.py` wraps `splitmuxsink` and calls the same `promote()`; it needs М9's bench.

## Prerequisites

- **М9 Lesson 4** — the spool, and *delete on acknowledgement, never on send*.
- **М9 Lesson 5** — the `segments` table and the timeline query this lesson replaces.
- **М9 Lesson 8** — *on restart, never resume the previous segment; open a new one.*
- **М11 Lesson 4** (read ahead if you like) — the epoch in the path. This lesson puts it there.

## Learning objectives

1. State the promotion order and say what each step protects.
2. Account for a kill mid-segment: what is promoted, what is picked up on restart, what is lost.
3. Replace the index table with a manifest and rebuild it from the files alone.
4. Mark a fenced epoch on a timeline and merge two resources' manifests.
5. Apply retention as a policy on the resource, in the order that survives a crash.
6. Say what a resource is, and why it has no controller.

---

## Step 1 — The paths

```
<spool>/<cam>/e<epoch>/<start>Z.mp4       the open segment, and closed ones not yet promoted
<archive>/<cam>/e<epoch>/<start>Z.mp4     promoted: the resource
<archive>/<cam>/manifest.jsonl            one line per promoted segment
```

The epoch is in every path from the first byte. `archivesink` gets it as a property from the worker when the camera starts (Lesson 4), and `splitmuxsink`'s `format-location` puts it in the directory name. Nothing downstream — promotion, the manifest, playback — has to be told about fencing; it reads the path.

```
parse("/a/7/e5/20260912T101000Z.mp4") -> (camera 7, epoch 5, 2026-09-12 10:10:00 UTC)
parse("/a/7/e5/manifest.jsonl")       -> None
```

## Step 2 — The acknowledgement order

`ArchiveResource.promote(spool_path)` is what the element calls on `splitmuxsink-fragment-closed`, and the order inside it is the lesson:

```
1. rename the closed segment into the archive      — atomic on one filesystem; on another, copy then appear whole
2. append its line to the manifest                  — {cam, epoch, start, end, path, bytes}
3. the spool copy is gone                           — a consequence of 1 on one filesystem; an explicit delete otherwise, last
```

The test asserts all three in that order: the spool path no longer exists, the archive path exists, the manifest has one line with the right epoch, a 600-second span and the byte count. A crash between 1 and 2 leaves a file no line names — `repair()` adds the line. A crash before 1 leaves a closed segment in the spool — the restart promotes it. There is no order of these steps in which footage that was written disappears.

## Step 3 — Kill it at minute seven

Ten-minute segments, a worker killed at minute seven of the eighth segment:

| | Where | What happens |
|---|---|---|
| six closed segments | promoted, in the archive, in the manifest | nothing; they were acknowledged |
| one closed segment | in the spool — closed, and the worker died before `promote()` | `closed_in_spool(grace=30 s)` finds it; the restarting worker promotes it first |
| the open segment | in the spool, being written | **lost** — up to one segment length, the number М9 Lesson 4 stated and М9 Lesson 8 repeated: *never resume it; open a new one* |

`test_kill_mid_segment_open_lost_closed_kept` builds exactly that spool and asserts the counts: seven lines after the restart's promotion, and `closed_in_spool` empty afterwards. The grace — two segment lengths in М11's sweep, thirty seconds here because the test controls the clock — is what tells a closed segment from an open one without asking the element: a file nobody has written to for that long is not being written.

## Step 4 — The manifest is the index

М9 Lesson 5's `segments` table had `camera_id, span, path, bytes, epoch` and a GiST index for the timeline query. `Manifest` has the same columns as one JSON line per segment, append-only, per camera, beside the footage:

```python
Manifest(archive, 7).append(seg)          # on promotion
Manifest(archive, 7).read()               # the rows
Manifest(archive, 7).timeline(t0, t1, current_epoch=4)
```

Two things the table could not do. **It returns with the disks**: when Server A comes back in М11, its manifests come back with its footage, and nothing is rebuilt by scanning. **It is rebuildable from the files alone** when it has to be — `repair()` walks the archive, adds a line for every segment no line names (epoch from the path, span from the name and the mtime), drops every line whose file is gone, and is idempotent:

```
delete the manifest; repair() -> {'added': 3, 'dropped': 0}     the same rows back, from the paths
delete one file;     repair() -> {'added': 0, 'dropped': 1}
                     repair() -> {'added': 0, 'dropped': 0}
```

That is М11's re-index sweep, renamed to what it now is: not a rebuild of an index that did not travel, but a repair of one that did.

## Step 5 — The fenced epoch, and two resources

The timeline marks what the epoch in the path makes visible. Camera 7 recorded under epoch 3, was taken over by a replacement under epoch 4, and the old instance kept writing for five minutes before its lease ran out:

```
timeline(10:05 → 10:30, current_epoch=4):
  10:00–10:10  e3  fenced       the last segment before the takeover
  10:10–10:15  e3  fenced       the zombie's — kept, marked, never deleted (М11 Lesson 4)
  10:10–10:20  e4               the live instance's
```

Nothing was deleted and nothing was corrupted: two writers, two epochs, two directories, one manifest that names both, sorted by start and then by epoch so the fenced one comes first. In М11 the same camera's footage may span two *resources* — Server A's until it died, Server B's after — and the console merges two manifests:

```
merged: e3, e3, e4  (resource A)  +  e5  (resource B)
```

## Step 6 — Retention is a policy on the resource

There is no controller for the archive, and there should not be: the only decisions about it are a policy — keep N days per camera — and a repair. `retain(cam, days, now)` deletes the file first and rewrites the manifest second, so a crash between the two leaves a line that names nothing (which `repair()` drops) rather than a file nothing names (which would be footage the timeline cannot find):

```
three segments on 1, 10 and 19 October; retain(days=8, now=20 October) -> 2 removed; usage 1000 bytes
```

`deploy/vms-archive-retain.timer` runs `repair()` and then `retain()` per camera every ten minutes, reading each camera's `retention_days` from the config store — the policy is the operator's, the enforcement is the resource's own, and the worker is not involved. The disk-full policies from М9 Lesson 8 (`stop_recording`, `degrade_retention`, `by_priority`) become the same policy with a high-water input — and in М11, a bucket quota.

**Deliverable:** record a file-camera for ten minutes with `archivesink`; kill the worker at minute seven; show six promoted, one closed-but-unpromoted picked up on restart, the open one lost; delete the manifest and rebuild it from the archive alone; then show a timeline with a fenced epoch on it.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `promote()` raises *not a segment path* | The element's `format-location` did not produce `<cam>/e<epoch>/<start>Z.mp4` — the `epoch` property was not set before the first fragment. The worker sets it (Lesson 4). |
| Segments appear in the archive without manifest lines | A crash between rename and append. `repair()` is for exactly this; the timer runs it. |
| The manifest has lines for files that are gone | Retention crashed between file and line, or somebody deleted files by hand. `repair()` drops them. |
| `closed_in_spool` promotes the open segment | The grace is shorter than a segment's write interval. Two segment lengths, as М11 chose; thirty seconds only in tests with a fake clock. |
| Playback finds a gap where the timeline shows a fenced segment | The player filtered `fenced: true` out. Fenced footage is real footage; show it, marked. |
| Promotion across filesystems is slow | The spool and the archive are on different disks: a copy, not a rename. Put the spool on the archive's filesystem, or accept the copy — it is the acknowledgement, and it is allowed to take time. |

## Recap

- The archive is a **resource**: server-bound, no controller, a policy and a repair.
- Promotion is М9 Lesson 4's rule pointed at ourselves: into the archive, then the line, then the spool copy goes.
- A kill loses the open segment and nothing else; a closed segment in the spool is picked up on restart.
- The manifest is the index, beside the footage: it returns with the disks and is rebuildable from them.
- The epoch is in every path; the timeline marks fenced footage and never deletes it; two resources merge.
- Retention deletes the file first and the line second, on a timer, from the operator's per-camera policy.

## Exercises

1. Reverse the promotion order — line first, then rename — and construct the crash that loses footage.
2. Set the grace to one second and run a real `archivesink` for five minutes. Count how many open segments the restart "promoted".
3. Write the manifest as one file per epoch instead of one per camera. What does `repair()` gain and what does `timeline()` lose?
4. Two workers promote into one camera's manifest at once (the reassignment window of Lesson 4). Show the interleaving that corrupts a line, then fix it with an append lock or per-epoch files, and say which М11 needs.
5. Implement `by_priority` from М9 Lesson 8 as a policy on the resource: which cameras lose days first when the disk is at 95 %, and why the worker must not know.

## Where this is going

There is a source and a sink and nothing running them. [**Lesson 4**](04-vmsworker-driverpack-as-the-worker.md) builds the worker — DriverPack itself — with М9's loop inside it, an epoch per camera taken by CAS, a lease, a heartbeat, and the property everything else in the course depends on: it restarts without asking anyone.
