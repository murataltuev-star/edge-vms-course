# Lesson 7 — What Is an SDK, and Why Run One in Docker?

**Module:** SDKs & Containers (Module 4)
**You will build:** a Docker image for `camera_sim.py`, and understand exactly what problem containers solve that a virtual machine or a plain script doesn't.
**Time:** ~60–75 minutes.

## Why this lesson exists

Every lesson so far has run plain Python directly on your machine. That's been fine because everything you've installed — FastAPI, Pydantic, Uvicorn — is a normal, pip-installable Python package: `pip install X`, seconds later it works, identically on macOS, Linux, or Windows. The real project needs two more SDKs, and they are **not the same kind of dependency**:

- **`boto3`** (Module 6) — a pip-installable Python package, same deal as FastAPI. Easy.
- **The AWS Kinesis Video Streams *Producer* SDK** — a C++ library that must be *compiled from source*, with real OS-specific failure modes (the reference spec documents a GCC version that OOM-kills the compiler on one base image and not another). It isn't on PyPI. `pip install` cannot help you. This is the SDK that makes `kvssink` — the GStreamer element the edge agent's pipeline depends on — exist at all.

That second kind of dependency is exactly the problem Docker exists to solve, and it's why this lesson comes before GStreamer rather than after: by the time you meet `kvssink` in Module 5, you'll already understand *why* it ships as a container instead of a `pip install` line.

## Prerequisites

- Lessons 1–6 completed. Docker itself needs no prior lesson's code, but this lesson containerizes `camera_sim.py` from Lesson 5, and calls back to its signal-handling behavior directly.
- Docker Desktop (or Docker Engine on Linux) installed and running: `docker version` should print both a Client and a Server section without errors.

## Learning objectives

1. Define what an SDK is, in general, and why some SDKs are a one-line install while others are a real build project.
2. Explain what a container actually is — and isn't — compared to a virtual machine.
3. Write a `Dockerfile`, build an image, and run, inspect, and stop a container.
4. Pass configuration and data into a container without baking either into the image: environment variables and volumes.
5. Recognize that `docker stop` is just `SIGTERM` under a different name — Lesson 5's signal handling applies unchanged.

---

## Step 1 — What an SDK actually is

Every cloud service — AWS, Stripe, GitHub, anything with an API — ultimately exposes a **raw API**: usually HTTP requests with JSON bodies, requiring a specific authentication scheme, specific error codes, specific pagination rules. You could call any of these directly: build the URL, set the headers, sign the request the way that service demands, send it, parse the JSON back by hand.

An **SDK** (Software Development Kit — here really meaning a *client library*) is code, usually published by the service itself, that wraps all of that in ordinary function calls in your own language. Where you might otherwise sign an HTTP request by hand and parse a JSON error body yourself, an SDK gives you an object with methods, and — this should sound familiar — turns the JSON response into a typed object, the exact job Pydantic does for your own API in Lessons 3 and 6. When you reach Module 6, `client.list_fragments(StreamName="cam-01")` is boto3 doing that translation for AWS's Kinesis Video Streams API, the same way `response_model=RecordingStatus` did it for yours.

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

Copy `camera_sim.py` from Lesson 5, unchanged, into a new folder. Add a file named exactly `Dockerfile` (no extension) beside it:

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
- `CMD ["python3", "camera_sim.py"]` — the default command a container starts with. Notice the shape: a **list** of strings, not one shell string — exactly the `argv` list you built for `Popen` in Lessons 5 and 6, and for exactly the same reason: no shell parses this, so nothing here is a place a stray character could be interpreted as an extra command.

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

`-d` detaches (runs in the background, prints a container ID, returns your prompt). `--name camera-1` gives it a name you choose instead of Docker's random one — you'll rely on this in Lesson 8, for the same reason the real spec insists on a deterministic container name. `docker ps` lists running containers — this is your `ps aux | grep camera_sim.py` from Lessons 5 and 6, one layer up, and just as real: `camera-1` is genuinely a process on your machine, just one Docker has put a namespace boundary around. `docker logs -f` tails its stdout — the container-level equivalent of watching `Popen`'s inherited stdout, except now the Docker daemon is the one holding onto that output for you to pull on demand, rather than it going straight to your terminal.

### Stop it

```bash
docker stop camera-1
```

`Ctrl+C` out of `docker logs -f` first (that only stops *watching* logs, not the container), then run `docker logs camera-1` once more. You should see `[camera] SIGTERM received, exiting cleanly` — the exact line `camera_sim.py`'s own signal handler prints in Lesson 5. **`docker stop` is `SIGTERM` under a different name**, with a grace period (10 seconds by default) before Docker escalates to `SIGKILL` if the process hasn't exited — precisely the escalation policy you implemented by hand in Lessons 5 and 6, now provided by the Docker daemon instead of your own code. Nothing about `camera_sim.py` needed to change to behave correctly under Docker; it already handled `SIGTERM` properly, so it already behaves correctly here.

A stopped container isn't gone — `docker ps -a` still lists it, using disk space, until you remove it:

```bash
docker rm camera-1
```

"Stopped but not removed" versus "removed" is the same distinction as Lesson 4's fake `_state` versus a truly absent recording — a container can exist without running, just like your process handle could report `running: false` without being deleted from memory.

## Step 4 — Configuration and data, without baking either into the image

An image is meant to be reused across environments and configurations — hardcoding a specific value (or worse, a credential) into a `Dockerfile` means it's now permanently embedded in every copy of that image, extractable by anyone who has it. Two mechanisms keep an image generic:

### Environment variables

```bash
docker run -e CRASH_AFTER=5 camera-sim
```

`-e CRASH_AFTER=5` sets an environment variable inside the container — the exact `CRASH_AFTER` your `camera_sim.py` already reads via `os.environ.get(...)` in Lesson 5, now supplied through Docker's flag instead of your shell's `export`. No code change needed; the mechanism is identical, only the delivery differs.

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

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop (or the Docker service on Linux) isn't running — start it, then retry. |
| `docker build` fails pulling `python:3.11-slim` | No network access, or a registry block — check your connection; this is the same category of failure as `pip install` needing PyPI reachable. |
| `docker run camera-sim` immediately exits with no ticks | Check `docker logs <container>` for a Python traceback — a typo in `CMD`'s filename is the most common cause. |
| Editing `media/label.txt` doesn't change the running container's output | Confirm you used `-v` (bind mount), not `COPY` in the `Dockerfile` — a `COPY`'d file is a one-time snapshot baked into the image, not a live link to your host. |
| `docker run --name camera-1 ...` fails with `name already in use` | A container by that name already exists (stopped or running) — `docker rm camera-1` first, or `docker rm -f camera-1` if it's still running. Keep this exact error in mind; Lesson 8 needs it. |

## Recap

- An SDK wraps a raw network API in ordinary function calls in your language — the same job Pydantic does for your own responses, applied to someone else's service.
- Pure-language SDKs (`boto3`) install in seconds via a package manager; native/compiled SDKs (the KVS Producer SDK behind `kvssink`) must be built for the exact machine running them, with real, OS-specific failure modes.
- Docker's core idea: build the hard thing once, package the *result*, and everyone else runs identical bytes instead of repeating a fragile build.
- A container is a namespaced process on your existing kernel, not a virtual machine with its own kernel — that's why it starts in milliseconds.
- `Dockerfile` → `docker build` produces an **image**; `docker run` produces a **container**, an instance of that image, the same relationship a class has to an object.
- `docker stop` sends `SIGTERM`, waits, then escalates to `SIGKILL` — identical to the escalation policy you wrote by hand in Lessons 5 and 6, now handled by the Docker daemon.
- `-e` passes configuration in as environment variables; `-v` bind-mounts real host files in as volumes — both keep an image generic and secret-free, deciding the specifics only at run time.

## Exercises

1. Add a `HEALTHCHECK` instruction to the `Dockerfile` (look up its syntax) that considers the container unhealthy if `camera_sim.py` hasn't printed a tick in the last 5 seconds — you'll need the container to write its last-tick timestamp somewhere `HEALTHCHECK`'s command can read.
2. Run two containers from the same image with different `--name`s and different `-e CRASH_AFTER=` values simultaneously; confirm via `docker ps` and `docker logs` that they're independent, and that stopping one doesn't affect the other.
3. Deliberately rebuild the image after changing only `media/label.txt` (not `camera_sim.py`) and confirm Docker's layer cache means the rebuild is instant — then explain in one sentence why that's true given `media/` was never `COPY`'d into the image at all.
4. Try `docker run --name camera-1 camera-sim` twice in a row without removing the first — reproduce the `name already in use` error from the troubleshooting table on purpose, and write down, before reading Lesson 8, what you think a supervisor script (like Lesson 5's `looper.py`) would need to do differently to avoid hitting this on every restart.

## Where this is going

Lesson 8 uses everything here — a `Dockerfile`, `-e`, `--name`, and the "stopping the client isn't the same as stopping the container" question Exercise 4 just raised on purpose — to extend Lesson 5's `looper.py` so it can run its child inside a container instead of as a plain subprocess. Nothing about `camera_sim.py` changes again; only how the supervisor launches it does.
