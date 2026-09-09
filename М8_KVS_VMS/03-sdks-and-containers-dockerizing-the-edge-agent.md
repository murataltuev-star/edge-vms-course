# Lesson 3 — SDKs and Containers — Dockerizing the Edge Agent

**Module:** KVS-VMS — a cloud VMS on Kinesis Video Streams (Module 8)
**You will build:** a Docker image for `camera_sim.py` and a precise understanding of what a container solves; then a `KVS_DOCKER_IMAGE` toggle for `looper.py` that runs the child in a container, survives an orphaned one, and never puts a credential in an argv.
**Time:** ~2.5–3 hours, in two parts.

> **This lesson is in 2 parts** — formerly Lessons 7–8 — and the step numbers run through all of them. Each part ends with its own troubleshooting table, recap and exercises; do the parts in order.

## Prerequisites

**Part A.**
- Lessons 1 and 2 completed. Docker itself needs no prior lesson's code, but this lesson containerizes `camera_sim.py` from Lesson 2, and calls back to its signal-handling behavior directly.
- Docker Desktop (or Docker Engine on Linux) installed and running: `docker version` should print both a Client and a Server section without errors.

**Part B.**
- Lesson 2 (the original `looper.py` and `camera_sim.py`) and Part A (Docker fundamentals, the `camera-sim` image, and its `--name` collision behavior from Exercise 4) both completed. This lesson assumes you can explain why `SIGKILL` can't be caught (Lesson 2, Step 3) — that fact reappears here, one layer up.

## Learning objectives

1. Define what an SDK is, in general, and why some SDKs are a one-line install while others are a real build project.
2. Explain what a container actually is — and isn't — compared to a virtual machine.
3. Write a `Dockerfile`, build an image, and run, inspect, and stop a container.
4. Pass configuration and data into a container without baking either into the image: environment variables and volumes.
5. Recognize that `docker stop` is just `SIGTERM` under a different name — Lesson 2's signal handling applies unchanged.
6. Wrap an existing subprocess call in `docker run` behind a single config toggle, without changing the surrounding supervisor logic.
7. Explain precisely why killing the `docker run` client process does not necessarily stop the container it started — and when it actually does.
8. Use a deterministic container name plus `docker rm -f`, run *before* every launch as well as on shutdown, to keep the supervisor's restart loop from failing forever.
9. Forward credentials into a container by variable name only, so a secret's value never appears in a process's own command line.
10. Read a non-trivial `Dockerfile` (the one behind `kvssink`) and explain what problem each of its non-default choices solves.

---

## Part A — What Is an SDK, and Why Run One in Docker?


Every lesson so far has run plain Python directly on your machine. That's been fine because everything you've installed — FastAPI, Pydantic, Uvicorn — is a normal, pip-installable Python package: `pip install X`, seconds later it works, identically on macOS, Linux, or Windows. The real project needs two more SDKs, and they are **not the same kind of dependency**:

- **`boto3`** (Module 6) — a pip-installable Python package, same deal as FastAPI. Easy.
- **The AWS Kinesis Video Streams *Producer* SDK** — a C++ library that must be *compiled from source*, with real OS-specific failure modes (the reference spec documents a GCC version that OOM-kills the compiler on one base image and not another). It isn't on PyPI. `pip install` cannot help you. This is the SDK that makes `kvssink` — the GStreamer element the edge agent's pipeline depends on — exist at all.

That second kind of dependency is exactly the problem Docker exists to solve, and it's why this lesson comes before GStreamer rather than after: by the time you meet `kvssink` in Module 5, you'll already understand *why* it ships as a container instead of a `pip install` line.
## Step 1 — What an SDK actually is

Every cloud service — AWS, Stripe, GitHub, anything with an API — ultimately exposes a **raw API**: usually HTTP requests with JSON bodies, requiring a specific authentication scheme, specific error codes, specific pagination rules. You could call any of these directly: build the URL, set the headers, sign the request the way that service demands, send it, parse the JSON back by hand.

An **SDK** (Software Development Kit — here really meaning a *client library*) is code, usually published by the service itself, that wraps all of that in ordinary function calls in your own language. Where you might otherwise sign an HTTP request by hand and parse a JSON error body yourself, an SDK gives you an object with methods, and — this should sound familiar — turns the JSON response into a typed object, the exact job Pydantic does for your own API in Lessons 1 and 2. When you reach Module 6, `client.list_fragments(StreamName="cam-01")` is boto3 doing that translation for AWS's Kinesis Video Streams API, the same way `response_model=RecordingStatus` did it for yours.

### Two very different kinds of SDK

This is the distinction that motivates the rest of this lesson:

- **Pure-language SDKs**, distributed through your language's normal package manager. `pip install boto3`, and seconds later it works, identically regardless of your OS. Nothing to compile.
- **Native/compiled SDKs** — usually C or C++, sometimes with system library dependencies of their own (OpenSSL, a logging library, an HTTP client). These must be *built* for your exact operating system and architecture before you can use them at all. AWS's Kinesis Video Streams Producer SDK is this kind: it is not on PyPI, `apt install` doesn't have it, and building it means compiling C++ against several other libraries — something that can fail in ways that have nothing to do with your own code, and everything to do with which OS and compiler version happen to be on the machine doing the building.

That second category is where things get genuinely painful, and it isn't hypothetical for this project: the reference spec documents, in its build notes, that building this exact SDK on one common base image OOM-kills the compiler outright — a failure that looks like a memory problem but is actually a toolchain problem, and would cost real time to diagnose on every machine that hits it independently.

### The problem, stated generally

If every developer on a team has to compile a finicky dependency themselves, you inherit *their* OS, *their* installed library versions, *their* available memory, *their* time lost to a build failure nobody else on the team will ever see. None of that has anything to do with whether your project's own code is correct. **Docker's answer: build the hard thing exactly once, in one controlled environment, and package the result — not the recipe — so everyone else runs the same bytes instead of repeating the build.**

## Step 2 — What a container actually is (and isn't)

A container is **not** a virtual machine. A VM emulates hardware and boots an entire second operating system, kernel included — that's why VMs take the better part of a minute to start and reserve a fixed chunk of memory whether they're using it or not.

A container is an ordinary process running on your existing Linux kernel — there's no second kernel, nothing to boot — given its own isolated *view* of the filesystem, its own process namespace (it can't see your other processes, only its own), and, usually, its own network namespace. The kernel enforces these boundaries directly. That's why a container starts in well under a second: there's no operating system to boot, just a process to launch wearing a kernel-enforced "costume."

Two terms, worth being precise about because they show up in every command in this lesson:

- An **image** is a read-only filesystem snapshot plus metadata about what command to run — a recipe's *finished output*, not the recipe itself.
- A **container** is a running (or stopped) instance of an image — the same relationship a Python *class* has to an *object*, or a script file to a running process. One image, many containers.

## Step 3 — Containerize `camera_sim.py`

Copy `camera_sim.py` from Lesson 2, unchanged, into a new folder. Add a file named exactly `Dockerfile` (no extension) beside it:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY camera_sim.py .
CMD ["python3", "camera_sim.py"]
```

Read this the way you read `main:app` in Lesson 1 — every line is doing a specific, literal thing:

- `FROM python:3.11-slim` — start from someone else's already-solved problem: an image that already has Python 3.11 installed on a small Debian base. You are not compiling Python from source; you're building *on top of* a base image, the exact same "build once, reuse the result" idea from Step 1, one layer further down.
- `WORKDIR /app` — every following instruction runs from this directory inside the image.
- `COPY camera_sim.py .` — copies a file from your **build context** (the folder you run `docker build` from) into the image at the current `WORKDIR`.
- `CMD ["python3", "camera_sim.py"]` — the default command a container starts with. Notice the shape: a **list** of strings, not one shell string — exactly the `argv` list you built for `Popen` in Lesson 2, and for exactly the same reason: no shell parses this, so nothing here is a place a stray character could be interpreted as an extra command.

### Build it

```bash
docker build -t camera-sim .
```

`-t camera-sim` tags the resulting image with a name so you can refer to it later instead of a hash. The trailing `.` is the build context — "everything Docker is allowed to `COPY` from starts here." Each instruction in the `Dockerfile` becomes a cached **layer**; rerun the build with no changes and Docker reuses every layer instantly instead of repeating the work. (A real-world habit worth knowing now: order instructions so that files which change *rarely* — like a `requirements.txt` you `pip install` from — come before files that change *often*, so an edit to your source code doesn't invalidate an expensive install step's cache.)

### Run it

```bash
docker run camera-sim
```

Ticks scroll in your terminal, exactly like running `python3 camera_sim.py` directly — a container's standard output is attached to your terminal by default, same as any other foreground process. `Ctrl+C` here sends `SIGINT` to the container the same way it would to a local process; you'll come back to exactly what receives that signal in Step 5.

Now run it detached, the way a long-lived service actually gets run:

```bash
docker run -d --name camera-1 camera-sim
docker ps
docker logs -f camera-1
```

`-d` detaches (runs in the background, prints a container ID, returns your prompt). `--name camera-1` gives it a name you choose instead of Docker's random one — you'll rely on this in Part B, for the same reason the real spec insists on a deterministic container name. `docker ps` lists running containers — this is your `ps aux | grep camera_sim.py` from Lesson 2, one layer up, and just as real: `camera-1` is genuinely a process on your machine, just one Docker has put a namespace boundary around. `docker logs -f` tails its stdout — the container-level equivalent of watching `Popen`'s inherited stdout, except now the Docker daemon is the one holding onto that output for you to pull on demand, rather than it going straight to your terminal.

### Stop it

```bash
docker stop camera-1
```

`Ctrl+C` out of `docker logs -f` first (that only stops *watching* logs, not the container), then run `docker logs camera-1` once more. You should see `[camera] SIGTERM received, exiting cleanly` — the exact line `camera_sim.py`'s own signal handler prints in Lesson 2. **`docker stop` is `SIGTERM` under a different name**, with a grace period (10 seconds by default) before Docker escalates to `SIGKILL` if the process hasn't exited — precisely the escalation policy you implemented by hand in Lesson 2, now provided by the Docker daemon instead of your own code. Nothing about `camera_sim.py` needed to change to behave correctly under Docker; it already handled `SIGTERM` properly, so it already behaves correctly here.

A stopped container isn't gone — `docker ps -a` still lists it, using disk space, until you remove it:

```bash
docker rm camera-1
```

"Stopped but not removed" versus "removed" is the same distinction as Lesson 1's fake `_state` versus a truly absent recording — a container can exist without running, just like your process handle could report `running: false` without being deleted from memory.

## Step 4 — Configuration and data, without baking either into the image

An image is meant to be reused across environments and configurations — hardcoding a specific value (or worse, a credential) into a `Dockerfile` means it's now permanently embedded in every copy of that image, extractable by anyone who has it. Two mechanisms keep an image generic:

### Environment variables

```bash
docker run -e CRASH_AFTER=5 camera-sim
```

`-e CRASH_AFTER=5` sets an environment variable inside the container — the exact `CRASH_AFTER` your `camera_sim.py` already reads via `os.environ.get(...)` in Lesson 2, now supplied through Docker's flag instead of your shell's `export`. No code change needed; the mechanism is identical, only the delivery differs.

### Volumes: mounting real files in from the host

An image's filesystem is sealed at build time — there's no way to `COPY` in a file that doesn't exist until runtime, like a specific video clip a particular student wants to test with. A **bind mount** solves this by attaching a folder from your actual machine into the running container:

```bash
mkdir -p media
echo "front door camera" > media/label.txt
```

Extend `camera_sim.py` (save as `labeled_camera.py`, or edit in place) to read it:

```python
import os
import pathlib
import signal
import sys
import time

_stop = False


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True


signal.signal(signal.SIGTERM, _handle_sigterm)

label_path = pathlib.Path("/media/label.txt")
label = label_path.read_text().strip() if label_path.exists() else "no label mounted"

tick = 0
print(f"[camera] starting (pid={os.getpid()}), label={label!r}", flush=True)
while not _stop:
    time.sleep(1)
    tick += 1
    print(f"[camera] tick {tick} label={label!r}", flush=True)
print("[camera] SIGTERM received, exiting cleanly", flush=True)
sys.exit(0)
```

Rebuild (update the `Dockerfile`'s `COPY`/`CMD` lines to match the new filename), then run with the folder mounted read-only:

```bash
docker run -d --name camera-2 -v "$(pwd)/media:/media:ro" camera-sim
docker logs -f camera-2
```

You should see `label='front door camera'` in every tick line — a file that exists only on your host machine, never copied into the image, read live by the container at `/media/label.txt`. `:ro` makes the mount read-only: the container can see the file but cannot modify your host's copy. This is precisely how the real project gets `clip.mp4` into the edge container — the reference spec's Docker mode bind-mounts the clip's directory read-only at `/media` inside the container, for exactly this reason: the video file is real data that belongs on the host, decided at run time, not something that should ever be baked into an image.

Edit `media/label.txt` while `camera-2` is still running, and watch the next tick line change — confirming the mount is live, not a one-time copy.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop (or the Docker service on Linux) isn't running — start it, then retry. |
| `docker build` fails pulling `python:3.11-slim` | No network access, or a registry block — check your connection; this is the same category of failure as `pip install` needing PyPI reachable. |
| `docker run camera-sim` immediately exits with no ticks | Check `docker logs <container>` for a Python traceback — a typo in `CMD`'s filename is the most common cause. |
| Editing `media/label.txt` doesn't change the running container's output | Confirm you used `-v` (bind mount), not `COPY` in the `Dockerfile` — a `COPY`'d file is a one-time snapshot baked into the image, not a live link to your host. |
| `docker run --name camera-1 ...` fails with `name already in use` | A container by that name already exists (stopped or running) — `docker rm camera-1` first, or `docker rm -f camera-1` if it's still running. Keep this exact error in mind; Part B needs it. |

### Recap

- An SDK wraps a raw network API in ordinary function calls in your language — the same job Pydantic does for your own responses, applied to someone else's service.
- Pure-language SDKs (`boto3`) install in seconds via a package manager; native/compiled SDKs (the KVS Producer SDK behind `kvssink`) must be built for the exact machine running them, with real, OS-specific failure modes.
- Docker's core idea: build the hard thing once, package the *result*, and everyone else runs identical bytes instead of repeating a fragile build.
- A container is a namespaced process on your existing kernel, not a virtual machine with its own kernel — that's why it starts in milliseconds.
- `Dockerfile` → `docker build` produces an **image**; `docker run` produces a **container**, an instance of that image, the same relationship a class has to an object.
- `docker stop` sends `SIGTERM`, waits, then escalates to `SIGKILL` — identical to the escalation policy you wrote by hand in Lesson 2, now handled by the Docker daemon.
- `-e` passes configuration in as environment variables; `-v` bind-mounts real host files in as volumes — both keep an image generic and secret-free, deciding the specifics only at run time.

### Exercises

1. Add a `HEALTHCHECK` instruction to the `Dockerfile` (look up its syntax) that considers the container unhealthy if `camera_sim.py` hasn't printed a tick in the last 5 seconds — you'll need the container to write its last-tick timestamp somewhere `HEALTHCHECK`'s command can read.
2. Run two containers from the same image with different `--name`s and different `-e CRASH_AFTER=` values simultaneously; confirm via `docker ps` and `docker logs` that they're independent, and that stopping one doesn't affect the other.
3. Deliberately rebuild the image after changing only `media/label.txt` (not `camera_sim.py`) and confirm Docker's layer cache means the rebuild is instant — then explain in one sentence why that's true given `media/` was never `COPY`'d into the image at all.
4. Try `docker run --name camera-1 camera-sim` twice in a row without removing the first — reproduce the `name already in use` error from the troubleshooting table on purpose, and write down, before reading Part B, what you think a supervisor script (like Lesson 2's `looper.py`) would need to do differently to avoid hitting this on every restart.

### Where this is going

Part B uses everything here — a `Dockerfile`, `-e`, `--name`, and the "stopping the client isn't the same as stopping the container" question Exercise 4 just raised on purpose — to extend Lesson 2's `looper.py` so it can run its child inside a container instead of as a plain subprocess. Nothing about `camera_sim.py` changes again; only how the supervisor launches it does.

---

## Part B — Dockerizing the Edge Agent


Lesson 2 built a supervisor for a plain subprocess. Part A showed how to containerize that same script. This lesson does the thing both were building toward: `looper.py` gains a single configuration toggle that switches its child from "a plain OS process" to "a Docker container," with almost no other code changing. Almost — because containers introduce one genuinely new failure mode that plain subprocesses don't have, and understanding it precisely is the actual point of this lesson, not a footnote.

This mirrors the real project's design directly: the reference spec's `KVS_DOCKER_IMAGE` environment variable does exactly this — empty means run `kvssink` on the host, set it to an image name and both `edge/looper.py` and `scripts/check_env.py` switch to container mode "with no other changes." You're building that toggle for real, against `camera_sim.py` instead of the real GStreamer pipeline, exactly as Lesson 2 used it as a stand-in for the same reason.
## Step 5 — One toggle, one small function

Everything in Lesson 2's `looper.py` stays as-is. Add a single environment-driven switch:

```python
import os

CHILD_SCRIPT = "camera_sim.py"
DOCKER_IMAGE = os.environ.get("KVS_DOCKER_IMAGE")   # unset/empty = host mode, exactly Lesson 2
STREAM_NAME = "cam-01"
CONTAINER_NAME = f"kvs-vms-edge-{STREAM_NAME}"


def _build_argv():
    if not DOCKER_IMAGE:
        return [sys.executable, CHILD_SCRIPT]
    return ["docker", "run", "--name", CONTAINER_NAME, DOCKER_IMAGE]
```

That's the entire "does this run in Docker" decision, in one function. Everything downstream — `subprocess.Popen(argv)`, `.wait()`, reading `.returncode` — is unchanged from Lesson 2, because a `docker run` invocation *is* just another command with an argv list, blocking until it exits, exactly like `python3 camera_sim.py` was. `CONTAINER_NAME` reuses `camera-sim`, the image you already built in Part A.

Build the image again if you haven't kept it: `docker build -t camera-sim .` in your Part A folder.

## Step 6 — Run it, and confirm the easy case works for free

Set the toggle and start the supervisor:

```bash
KVS_DOCKER_IMAGE=camera-sim python3 looper.py
```

In a second terminal, `docker ps` should show `kvs-vms-edge-cam-01` running, and `looper.py`'s own terminal should be showing `[camera] tick N` lines — the container's stdout, attached to the `docker run` client (foreground, no `-d`), attached in turn to `looper.py`'s own inherited stdout, exactly the chain Lesson 2 relied on for the plain-subprocess case.

Now press `Ctrl+C`. Watch it shut down cleanly — same log lines as Lesson 2, same clean exit. Confirm with `docker ps` in the second terminal: no container running. **Nothing about your shutdown-handling code changed, and it still works.** Here's why, precisely: `_request_shutdown` calls `_current_proc.terminate()`, which sends `SIGTERM` to `_current_proc` — but in Docker mode, `_current_proc` is the `docker run` *client* process, not the container. Docker's client has a feature called signal-proxying, on by default: a signal the client process itself receives and gets the chance to handle, it forwards into the container. `camera_sim.py` already has a `SIGTERM` handler (Lesson 2), so it exits cleanly inside the container, the container stops, the attached client sees that and exits too, and your `.wait()` unblocks — the entire chain nobody had to build.

## Step 7 — Reproduce the failure mode this lesson is really about

That phrase above — "a signal the client process itself receives and gets the chance to handle" — is the load-bearing part. Cause the case where that doesn't happen, on purpose.

Start the supervisor again the same way. In the second terminal, find the **client's** pid — not the container's:

```bash
ps aux | grep "docker run" # list all the processes started with docker run
```

Send it `SIGKILL`, not `SIGTERM`:

```bash
kill -9 <that pid>
```

Watch `looper.py`'s log: its `.wait()` unblocks almost immediately (its direct child, the client process, just died), and it logs something like `killed by signal 9`. Now check `docker ps` in the second terminal. **The container is still there, still running.** `looper.py` believes the recording stopped — its own log says so — but it didn't.

This is the exact scenario Lesson 2's Step 7 table warned about, one layer higher up: `SIGKILL` cannot be caught, by anything, ever. The `docker run` client's signal-proxying feature is application-level code *inside* the client — code that only runs if the client is given the chance to run its own signal handler. A `SIGKILL`'d process never runs another line of its own code, so there is no proxying, no forwarding, nothing. And separately from that: the container was never the client's child in the OS process-tree sense to begin with — it's a process the **Docker daemon** launched and continues to own, with the client acting only as a messenger for as long as it's alive. Kill the messenger, and the thing it was relaying to doesn't hear about it — not because the message was slow, but because there was never a direct line between them in the first place.

Confirm the orphan is real and unaffected: wait a few seconds, check `docker logs kvs-vms-edge-cam-01` — ticks are still incrementing, the process never stopped.

## Step 8 — Why the pre-loop cleanup isn't optional

Leave that orphan running, and let `looper.py` (which is still executing — it thinks the loop ended, and per its own backoff logic will try to restart) attempt its next iteration. Watch the log. Without any extra code, the *next* `docker run --name kvs-vms-edge-cam-01 camera-sim` will fail outright:

```
docker: Error response from daemon: Conflict. The container name "/kvs-vms-edge-cam-01" is already in use...
```

— exactly the error you deliberately caused in Part A's Exercise 4, now happening automatically, forever, on every retry, because the orphan from Step 7 is still holding that name. Fix it the way the real spec requires: remove any container by that name **before every launch**, not only on shutdown.

```python
def _remove_stale_container():
    """`docker run --name` fails outright if a container of that name still
    exists, running or merely stopped -- an orphan left by Step 7 would
    otherwise fail every subsequent restart forever. This is not redundant
    with the shutdown cleanup below; it runs before every single iteration."""
    if DOCKER_IMAGE:
        subprocess.run(
            ["docker", "rm", "-f", CONTAINER_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
```

Call it at the top of `run_child_once()`, before building the argv, and once more in a `finally` block wrapped around `main()`'s loop, so a clean shutdown also guarantees no container survives the supervisor itself:

```python
def run_child_once():
    global _current_proc
    _remove_stale_container()
    argv = _build_argv()
    _current_proc = subprocess.Popen(argv)
    if _shutting_down:
        _current_proc.terminate()
    started = time.monotonic()
    _current_proc.wait()
    duration = time.monotonic() - started
    returncode = _current_proc.returncode
    _current_proc = None
    return returncode, duration


def main():
    backoff = BACKOFF_START
    loop_num = 0
    try:
        while not _shutting_down:
            loop_num += 1
            label = f" (docker image {DOCKER_IMAGE})" if DOCKER_IMAGE else ""
            _log(f"loop {loop_num} started{label}")
            returncode, duration = run_child_once()

            if _shutting_down:
                _log(f"loop {loop_num} stopped after {duration:.1f}s (shutdown)")
                break

            if returncode == 0:
                _log(f"loop {loop_num} exited cleanly after {duration:.1f}s, restarting")
                backoff = BACKOFF_START
                continue

            if returncode < 0:
                _log(f"loop {loop_num} killed by signal {-returncode} after {duration:.1f}s")
            else:
                _log(f"loop {loop_num} failed (exit {returncode}) after {duration:.1f}s")

            _log(f"backing off {backoff:.0f}s before retry")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_CAP)
    finally:
        _remove_stale_container()
        _log("stopped")
```

`docker rm -f` talks directly to the Docker **daemon** — not to any client process, alive or dead — which is exactly why it's the right tool here: it doesn't depend on anything being alive to proxy a signal through. It works whether the target container is running, orphaned, or already stopped, which is what makes it safe to run unconditionally before every single iteration rather than only when you suspect a problem.

Repeat Step 7's `kill -9` test with this version. `looper.py` should log the same `killed by signal 9`, but the *next* loop iteration should now succeed cleanly — `docker ps` briefly shows no container (removed), then a fresh one under the same name.

## Step 9 — Credentials never belong in an argv

The real edge agent needs AWS credentials inside the container. The tempting way to pass them:

```python
return ["docker", "run", "--name", CONTAINER_NAME,
        "-e", f"AWS_SECRET_ACCESS_KEY={os.environ['AWS_SECRET_ACCESS_KEY']}",
        DOCKER_IMAGE]
```

Don't do this. Whatever you put after `-e KEY=`, value included, becomes part of this process's own command line — and a command line is visible to `ps -eo pid,args`, the exact output Lesson 2 scanned to *find* a process by what's in its arguments. A secret written there is readable by anyone on the machine who can run `ps aux`, or inspect `/proc/<pid>/cmdline` directly.

The fix is a `-e` flag with **no value**:

```python
def _build_argv():
    if not DOCKER_IMAGE:
        return [sys.executable, CHILD_SCRIPT]
    argv = ["docker", "run", "--name", CONTAINER_NAME]
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION"):
        if var in os.environ:
            argv += ["-e", var]        # name only -- no "=value" here, ever
    argv.append(DOCKER_IMAGE)
    return argv
```

`-e VAR` with a bare name tells Docker "read this variable's *value* from my own environment, and set the same variable inside the container" — the value is passed through the daemon's API call, never typed into this argv list at all. Confirm it: export a dummy value, start the supervisor in Docker mode, and in the second terminal run `ps -eo pid,args | grep "docker run"`. You'll see `-e AWS_ACCESS_KEY_ID` in the output — the bare name, never the value.

(The real spec forwards `AWS_SESSION_TOKEN` this same way unconditionally, relying on the fact that Docker simply omits a variable that was never set in the parent's environment rather than erroring — a small simplification over checking `in os.environ` first, equally safe either way.)

## Step 10 — Reading material: the real `kvssink` Dockerfile

You are not building this image in this lesson — per the reference spec, it's a genuine 20–40 minute compile of a C++ SDK from source, wants real memory headroom, and has failure modes that have nothing to do with anything you've learned so far. What follows is the Dockerfile that build produces, annotated against the reference spec's own build notes, so that when Module 5 hands you `gst-launch-1.0` and a pipeline that includes `kvssink`, you already understand where that element came from and why it doesn't just `apt install`.

```dockerfile
# Two departures from AWS's own sample Dockerfile are load-bearing here --
# see the annotations below before "simplifying" either one back.

FROM ubuntu:22.04
# NOT amazonlinux:2. That image's GCC (7.3.1) OOM-kills the compiler (cc1plus)
# partway through building one of the dependencies below, even with plenty of
# host memory free. The failure ("cannot allocate memory") reads exactly like
# a resource limit -- it is actually a toolchain problem, and costs real time
# to diagnose the first time you hit it. Ubuntu 22.04's newer GCC doesn't.

RUN apt-get update && apt-get install -y \
    build-essential cmake git pkg-config \
    libssl-dev liblog4cplus-dev libcurl4-openssl-dev \
    gstreamer1.0-tools gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    x264 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
# log4cplus / OpenSSL / curl come from apt here, matched by
# -DBUILD_DEPENDENCIES=OFF below: the dependency that would otherwise need to
# be compiled from source inside this image is linked from a package instead.
# x264 + ffmpeg: lets this image generate a synthetic test clip when the host
# has none -- the alternative is requiring a full media toolchain on every
# machine just to produce a 60-second test file.

ARG BUILD_JOBS=2
# NOT $(nproc). Docker Desktop reports every host CPU core to the container
# while actually granting only a fraction of host RAM -- budget roughly
# 1.5GB per parallel compile job, or `make -j` OOMs for the same underlying
# reason the wrong base image did above, just later in the build.

RUN git clone --depth 1 \
      https://github.com/awslabs/amazon-kinesis-video-streams-producer-sdk-cpp.git \
      /opt/kvs-sdk \
    && mkdir -p /opt/kvs-sdk/build \
    && cd /opt/kvs-sdk/build \
    && cmake .. -DBUILD_GSTREAMER_PLUGIN=ON -DBUILD_DEPENDENCIES=OFF \
    && make -j"${BUILD_JOBS}"

ENV GST_PLUGIN_PATH=/opt/kvs-sdk/build
# kvssink is not part of stock GStreamer -- this is the line that makes
# `gst-inspect-1.0 kvssink` (and the real pipeline's own kvssink element)
# resolve at all, inside a container built from this image.

WORKDIR /app
ENTRYPOINT ["gst-launch-1.0", "-q"]
```

Read the `ENTRYPOINT` line as a preview, not a mystery to solve now — Module 5 is entirely about what `gst-launch-1.0` is and how to read (and write) the pipeline that follows it. For this lesson, the point is narrower and already familiar: this `Dockerfile` exists, with every one of its non-default choices deliberate, for exactly the reason Step 5 of Part A introduced in the abstract — a compiled dependency with real, OS-specific failure modes gets built once, correctly, and shipped as an image, so nobody downstream repeats the compile (or its failures) themselves.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `docker: Error response from daemon: Conflict. The container name ... is already in use` | `_remove_stale_container()` is missing from the top of `run_child_once()`, or only runs on shutdown — it must run before every iteration. |
| Killing the client with `kill -9` doesn't leave an orphan on your machine | Check you found the **client's** pid (`ps aux | grep "docker run"`), not the container's own process — killing the wrong one won't demonstrate anything. |
| `docker ps` still shows the container after a clean `Ctrl+C` | Confirm `camera_sim.py` inside the image still has its `SIGTERM` handler from Lesson 2 — without it, the proxied signal still stops the container (default action for an unhandled `SIGTERM` is termination), but you won't see the "exiting cleanly" log line. |
| Credentials still visible in `ps` output | Double check you're passing `-e VAR` with no `=value` — a single leftover `-e VAR=value` anywhere in `_build_argv` defeats the whole point. |
| `cc1plus` killed / `cannot allocate memory` while reading Step 10 | This is the exact base-image gotcha the annotations describe — it's why the Dockerfile uses `ubuntu:22.04` and caps `BUILD_JOBS`, not a sign anything here is wrong. |

### Recap

- Wrapping a child in `docker run` needed only one new function (`_build_argv`) — every other line of the supervisor from Lesson 2 was already generic enough to not care what it was supervising.
- `docker run`'s client proxies signals it receives (and gets to handle) into the container it started — which is why a plain `SIGTERM`/`Ctrl+C` shutdown works with zero extra code.
- That proxying is application-level code inside the client; a `SIGKILL`'d client never runs it, and the container — which the Docker daemon owns, not the client — is left running, orphaned. Same uncatchable-signal fact from Lesson 2, one layer up.
- A deterministic container name plus `docker rm -f`, run before every launch (not only on shutdown), is what keeps an orphan from turning into a permanent restart failure.
- `-e VAR` with no value forwards a credential's value through the Docker daemon directly; `-e VAR=value` leaks it into a process's own command line, visible to anyone who can run `ps`.
- A compiled SDK with real build failure modes (like the one behind `kvssink`) gets built once, deliberately, into an image — every non-default choice in that `Dockerfile` is answering a specific failure the defaults would otherwise hit.

### Exercises

1. Add a `docker inspect --format '{{.State.Status}}' kvs-vms-edge-cam-01` check to `_remove_stale_container` that logs whether it actually found (and removed) a stale container, versus there being nothing to clean up — useful for confirming Step 8's fix is doing something on a given run, not just assumed to be.
2. The real spec bind-mounts a clip's directory read-only at `/media` inside the container (Part A, Step 8) and rewrites the `location=` path accordingly. Extend `_build_argv` to add `-v` for a local `media/` folder, and change `camera_sim.py` (or reuse `labeled_camera.py` from Part A) to read something from it — confirm the mounted content is visible inside the container the same way it was in Part A's standalone test.
3. `docker run`'s signal-proxying is a convenience, not a guarantee your code should quietly depend on forever. Sketch (comments are enough) how `run_child_once` could explicitly call `docker stop <name>` — which talks to the daemon directly, the same way `docker rm -f` does — instead of relying on `_current_proc.terminate()` plus proxying, and explain in a sentence why that might be more robust for the real project's Docker mode specifically.
4. In your own words: why does the annotation on `ARG BUILD_JOBS=2` say Docker Desktop "reports every host CPU core... while actually granting only a fraction of host RAM"? What specifically would go wrong if this Dockerfile used `make -j$(nproc)` on a modest laptop instead?

## Where this is going

The `ENTRYPOINT ["gst-launch-1.0", "-q"]` line in Step 10 is where this lesson hands off to the next one. Module 5 opens `gst-launch-1.0` up properly: what a GStreamer *pipeline* actually is, what each element in the real project's pipeline does (`filesrc`, `qtdemux`, `h264parse`, `identity sync=true`, and finally `kvssink` itself), and why a file read from disk needs deliberate pacing to look like a live camera feed at all. `camera_sim.py` retires there — replaced, at last, by the real thing it was always standing in for.
