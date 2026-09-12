# vmsnode — М10, whole: the platform's shape on one box

The five lessons as one runnable package. No scheduler, no KVS, no database: two stores on disk, a controller that is the only writer, a worker that is DriverPack, an archive that is a resource — and a second, trivial subsystem that proves the platform knows nothing about video.

```
vmsnode/
  vmsplatform/                 the platform (named so because Python owns `platform`)
    variables.py               Lesson 1  a config store with ModifyIndex and check-and-set, as files; one writer per prefix
    objects.py                 Lesson 1  an object store: a directory
    epoch.py                   Lesson 1  the fencing-token issuer and the lease — generic
    contract.py                Lesson 1  Subsystem, Assignment, Heartbeat; the Controller and Worker bases
  vms/                         the VMS — the first subsystem
    reconciler.py              Lesson 4  М9 Lesson 6's loop, copied unchanged: the contract
    archive.py                 Lesson 3  the archive resource: spool → promote → manifest; repair; retention as a policy
    worker.py                  Lesson 4  vmsworker: N pipelines against an assignment; an epoch per camera; a lease; the heartbeat
    controller.py              Lesson 5  vmscontroller: cameras and placement by CAS; what it refuses; rebalance on request
    console.py                 Lesson 5  the one-box console, standard library: the read model from heartbeats; writes to the controller
    config.py                  the schema, as items in the config store
    __main__.py                python3 -m vms worker | controller
  gstvms/                      Track 2 — needs GStreamer
    uri.py                     Lesson 2  driverpack://file/<name> resolved and refused — pure, no GStreamer
    driverpacksrc.py           Lesson 2  the element: looping, PTS rebased across the loop
    archivesink.py             Lesson 3  splitmuxsink into the spool; on fragment-closed, promote
    actuator.py                Lesson 4  driverpacksrc ! h264parse ! watchdog ! tee ! archivesink, per camera
  deploy/                      systemd: vmscontroller.service, vmsworker@.service, the archive policy on a timer
  tests/                       34 tests, milliseconds, no GStreamer
```

```bash
python3 tests/run.py                                   # 34 tests
PLATFORM_DIR=/data/platform python3 -m vms controller  # the console on :8080
WORKER_NAME=w-1 python3 -m vms worker                  # with GStreamer: records; without: the fake actuator
```

## What each lesson's deliverable became

| Lesson | Deliverable | Test |
|---|---|---|
| 1 | a config store that survives a restart and refuses a stale CAS; one writer per prefix; the contract a second team could implement | `test_lesson1_platform.py` — including *the platform knows nothing about video* (no import from `vms/`, and not the word) |
| 2 | `driverpacksrc` running for an hour with monotonic PTS; the refusal of a vendor URI | `test_lesson2_driverpacksrc.py` — the URI logic here; the element and the hour on a box with GStreamer |
| 3 | kill the worker at minute seven: six promoted, one closed-but-not-promoted picked up on restart, the open one lost; rebuild the manifest from the files | `test_lesson3_archive.py` — the acknowledgement order, `closed_in_spool`, `repair()`, the fenced epoch on the timeline, two resources merged, retention |
| 4 | М9's four failures against the worker with its tests passing unchanged; the zombie on one box | `test_lesson4_worker.py` — М9 Lesson 6's seven, then the assignment, the epoch per camera, the restart with the controller stopped, the zombie, the reassignment that is not one |
| 5 | one box, two subsystems, one console; the controller stopped, the worker killed, recording resumes | `test_lesson5_controller.py` and `test_second_subsystem.py` — refusals, stored placement, adding a worker moves nothing, two controllers agree, the failure arithmetic, the console over HTTP, the counter subsystem |

## The two lines the code holds

**The controller is never on the recovery path.** `test_restart_with_the_controller_stopped` deletes the controller object, starts a fresh worker under the same name, and asserts it records — from its assignment, with the next epoch for each camera.

**The platform knows nothing about video.** `test_the_platform_knows_nothing_about_video` greps `vmsplatform/` for an import from `vms/` and for the word *camera*, and `test_second_subsystem.py` runs a controller and a worker that count seconds through the same base classes with a different prefix.

## Verified where

The 34 tests ran in the authoring sandbox (Python 3.11) and on the author's machine (3.10). `gstvms/` — the two elements and the actuator — is written to GStreamer's Python binding and not exercised here; the logic it calls (`vms.archive.ArchiveResource.promote`, the URI resolution) is. The hour-long PTS run, `kill -9` mid-segment on real files, and the zombie with two real worker processes are the box's.
