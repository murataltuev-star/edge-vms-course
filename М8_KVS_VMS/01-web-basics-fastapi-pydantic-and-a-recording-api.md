# Lesson 1 — Web Basics — FastAPI, Routes, Pydantic, and a Recording-Status API

**Module:** KVS-VMS — a cloud VMS on Kinesis Video Streams (Module 8)
**You will build:** a web server that answers HTTP requests; routes that take input from the URL; an API that validates what it accepts and shapes what it returns; and an in-memory GET/POST recording-status API with the real project's idempotency and `409` semantics.
**Time:** ~4–5 hours, in four parts.

> **This lesson is in 4 parts** — formerly Lessons 1–4 — and the step numbers run through all of them. Each part ends with its own troubleshooting table, recap and exercises; do the parts in order.

## Prerequisites

**Part A.**
- Python 3.11 or newer installed (`python3 --version` to check).
- Comfortable with basic Python: functions, running scripts from a terminal.
- No prior web development experience assumed.

**Part B.**
- Part A completed: you can create, run, and query a FastAPI app.

**Part C.**
- Part A and Part B completed.

**Part D.**
- Part A and Part B and Part C completed. This lesson assumes you're fluent with routes, path/query parameters, `BaseModel`, and `response_model` — it won't re-explain them.

## Learning objectives

1. Explain, in your own words, what a web server does when a browser "visits" it.
2. Set up an isolated Python environment for a project.
3. Install and run **FastAPI** (the web framework) and **Uvicorn** (the server that runs it).
4. Write and run a minimal FastAPI application with a single route.
5. Use FastAPI's automatic interactive documentation to test your API without writing a browser UI.
6. Distinguish path parameters from query parameters and know when to use each.
7. Declare typed parameters and get automatic validation for free.
8. Make query parameters optional, with defaults.
9. Return lists and nested JSON structures, not just flat dicts.
10. Understand and deliberately choose HTTP status codes.
11. Explain what Pydantic does and why FastAPI is built around it.
12. Define a `BaseModel` describing the shape of expected data.
13. Accept a validated JSON body with `POST`.
14. Read and act on FastAPI's automatic `422` validation errors.
15. Use `response_model` to guarantee the *shape* of what your API sends back, not just what it accepts.
16. Use `Optional` fields, defaults, and nested models.
17. Model a small piece of *server-side state* correctly, and know why this is unusual compared to Part A and Part B and Part C.
18. Implement an **idempotent** start operation — one that's safe to call more than once.
19. Return a `409 Conflict` deliberately, and explain why 409 and not 400 or 404.
20. Read and simulate the "who owns this" distinction the real project calls `managed`.
21. Test a stateful API by making a *sequence* of requests, not just one.

---

## Part A — Your First FastAPI App


Later in this course you will build the backend for a real video surveillance system — the thing that lets a browser ask "what footage exists?" and "let me watch this moment." That backend is a **web server**. Before touching any of that, you need to be able to answer a much smaller question: *what actually happens between a browser and a piece of Python code?*

This lesson answers that question by building the smallest possible web app, three times, each time understanding one more layer of what's going on.
## Step 0 — What is a web app, really?

Skip this if you've built one before; read it if you haven't.

A web app is two programs talking to each other over a network:

```
┌─────────────┐        HTTP request         ┌─────────────┐
│   Client    │  ───────────────────────▶   │   Server    │
│ (browser,   │   GET /hello                │ (your code) │
│  curl, app) │                             │             │
│             │  ◀───────────────────────   │             │
└─────────────┘        HTTP response        └─────────────┘
                  200 OK
                  {"message": "hi"}
```

- The **client** sends a **request**: a method (`GET`, `POST`, ...), a path (`/hello`), and optionally a body.
- The **server** sends back a **response**: a status code (`200` = OK, `404` = not found, ...) and a body — usually JSON these days.
- Nothing is remembered between requests unless the server deliberately stores it somewhere. HTTP is stateless by default.

That's the entire mental model. Everything in this lesson is a variation on "receive a request, do something, send a response."

---

## Step 1 — Set up an isolated project

Never install packages into your system Python. Every project gets its own **virtual environment** — a private folder holding just that project's packages, so different projects can use different (even conflicting) versions of the same library without interfering with each other.

```bash
mkdir fastapi-intro
cd fastapi-intro
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows (PowerShell: .venv\Scripts\Activate.ps1)
```

Your prompt should now show `(.venv)` at the start of the line. That's your signal you're working *inside* the virtual environment, not your system Python.

> **Checkpoint:** run `which python3` (macOS/Linux) or `where python` (Windows). The path should point *inside* `fastapi-intro/.venv`. If it doesn't, the environment isn't activated — re-run the `source`/`activate` command above.

## Step 2 — Install FastAPI and Uvicorn

```bash
pip install fastapi uvicorn
```

Two different jobs, two different packages:

- **FastAPI** is the *framework*: it gives you a way to describe routes ("when a GET request comes in for `/hello`, run this function") and it handles turning Python objects into JSON.
- **Uvicorn** is the *server*: the actual program that opens a network socket, listens for incoming HTTP connections, and hands each request to FastAPI. FastAPI never listens on a socket by itself — it needs Uvicorn (or something like it) to run.

Think of FastAPI as the receptionist who knows what to do with each visitor, and Uvicorn as the front door.

## Step 3 — Write the smallest possible app

Create a file called `main.py`:

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def read_root():
    return {"message": "Hello from your first API"}
```

Three things happening here:

- `app = FastAPI()` creates the application object. Everything else attaches to `app`.
- `@app.get("/")` is a **decorator** — it registers the function below it as the handler for `GET` requests to the path `/`.
- The function returns a plain Python `dict`. FastAPI converts it to JSON automatically. You never call `json.dumps` yourself.

## Step 4 — Run it

```bash
uvicorn main:app --reload
```

Read that command literally: `main` is the filename (`main.py` without the extension), `app` is the variable name you created inside it. `--reload` tells Uvicorn to restart automatically whenever you save a change to the file — invaluable while learning, but you'll turn it off in production later in the course.

You should see something like:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Application startup complete.
```

Leave this running. Open a **second** terminal for the next step (or a browser tab).

## Step 5 — Talk to it

**In a browser:** go to `http://127.0.0.1:8000/`. You should see:

```json
{"message": "Hello from your first API"}
```

**From the command line**, in your second terminal:

```bash
curl http://127.0.0.1:8000/
```

Same JSON comes back. This matters: *a browser is just one kind of client.* Anything that can make an HTTP request — curl, a Python script, another server, the hls.js video player you'll use later in this course — can talk to your API the same way.

## Step 6 — The docs you get for free

Go to `http://127.0.0.1:8000/docs`.

This is **Swagger UI**, and you didn't write a single line to get it. FastAPI inspects every route you define and generates this interactive page automatically. Click on the `GET /` entry, click **Try it out**, click **Execute** — you just made a request without curl or a browser address bar.

There's a second one at `http://127.0.0.1:8000/redoc` — a read-only, more document-like view of the same information. `/docs` is the one you'll live in while building and testing; `/redoc` is the one you'd hand to someone consuming your API.

> **Why this matters for later:** every endpoint you add for the rest of this course shows up here automatically, fully described, testable, with no extra work. Get comfortable with `/docs` now — you'll use it constantly.

## Step 7 — Add a second route

Edit `main.py`:

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def read_root():
    return {"message": "Hello from your first API"}


@app.get("/health")
def health_check():
    return {"status": "ok"}
```

Save the file. Look at the terminal running Uvicorn — because of `--reload`, it noticed the change and restarted on its own. Refresh `/docs` in your browser: the new `GET /health` route is there without you restarting anything by hand.

`/health` is a real, common pattern: a cheap endpoint whose only job is to answer "is the server alive?" — useful for monitoring tools, load balancers, and (later) your own scripts.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `command not found: uvicorn` | Virtual environment not activated, or install failed — re-run Step 1 then Step 2. |
| `ModuleNotFoundError: No module named 'fastapi'` | Same as above — you're running a Python outside the venv. |
| `Address already in use` on startup | Something else is already listening on port 8000. Stop it, or run `uvicorn main:app --reload --port 8001`. |
| Browser shows `{"detail":"Not Found"}` | You requested a path with no matching route — check for typos, and remember paths are case-sensitive. |
| Editing `main.py` does nothing | You forgot `--reload`, or you're editing a different copy of the file than the one you ran Uvicorn from. |

### Recap

- A web app is a client and a server exchanging HTTP requests and responses; the server is stateless by default.
- Virtual environments keep each project's dependencies isolated.
- FastAPI describes *what* happens for each route; Uvicorn is the server that actually *runs* it and listens on the network.
- `@app.get(path)` registers a handler function for that path.
- Returning a `dict` is enough — FastAPI serializes it to JSON for you.
- `/docs` gives you a working test UI for free, generated from your code.

### Exercises

1. Add a third route, `GET /about`, that returns your name and one fact about you as JSON.
2. Change the message returned by `/` and confirm (via the still-running `--reload` server) that you see the change without restarting Uvicorn yourself.
3. Stop the server (`Ctrl+C`) and start it again, this time on port `9000`. Confirm `/docs` works at the new port.
4. In your own words (2–3 sentences, no looking back at this document), explain to a partner or write down: what job does FastAPI do, and what job does Uvicorn do?

**Next part:** routes get more useful once they can take input. Part B covers path and query parameters — how `/items/42?verbose=true` gets turned into typed Python values automatically.

---

## Part B — Routes, Path Parameters, and Query Parameters


The backend you'll build later in this course has an endpoint like `GET /api/fragments?start=1756382400&end=1756386000` — a fixed path with variable values attached. Right now your routes take zero input. This lesson closes that gap: how does a value typed into a URL end up as a properly-typed Python variable inside your function?
## Step 8 — Start from a fresh app

Continue in the same `fastapi-intro` project from Part A (same virtual environment, activated). Replace the contents of `main.py`:

```python
from fastapi import FastAPI

app = FastAPI()

cameras = [
    {"id": "cam-01", "location": "front door", "recording": True},
    {"id": "cam-02", "location": "garage", "recording": False},
]


@app.get("/cameras")
def list_cameras():
    return cameras
```

Run it (`uvicorn main:app --reload`) and check `http://127.0.0.1:8000/cameras`. You get back a JSON **array** this time, not an object — FastAPI is just as happy serializing a `list` as a `dict`. `cameras` here is an ordinary in-memory Python list; there's no database yet, and there won't be one in this course's MVP either. Restarting the server resets it. That's a deliberate simplification, not an oversight — real persistence is a topic for later.

## Step 9 — Path parameters

You want `GET /cameras/cam-01` to return just that one camera. The `{camera_id}` value is *part of the path itself*, not something tacked on after a `?`.

```python
@app.get("/cameras/{camera_id}")
def get_camera(camera_id: str):
    for camera in cameras:
        if camera["id"] == camera_id:
            return camera
    return {"error": "not found"}
```

The name inside `{curly braces}` in the decorator **must match** the parameter name in the function signature — `camera_id` in both places. FastAPI reads the path template, matches it against the incoming URL, extracts the value, and passes it to your function as a normal argument.

Try `http://127.0.0.1:8000/cameras/cam-01` and `http://127.0.0.1:8000/cameras/cam-99`. The second one hits your `"not found"` branch — but returns it with a `200 OK` status, which is misleading. You'll fix that in Step 12.

### Type coercion, not just extraction

Change the signature to expect an integer:

```python
@app.get("/replay/{minute}")
def replay_at_minute(minute: int):
    return {"you_asked_for_minute": minute, "type": str(type(minute))}
```

Visit `/replay/42` — you get back `{"you_asked_for_minute": 42, "type": "<class 'int'>"}`. Note that: `42`, an actual Python `int`, not the string `"42"`. FastAPI read your type hint (`minute: int`) and converted the URL text for you.

Now visit `/replay/soon`. You get a `422 Unprocessable Entity` error with a JSON body explaining exactly what went wrong — you never wrote a single `try`/`except` or an `if isinstance(...)` check. **This is the core idea you'll rely on for the rest of the course: type hints in FastAPI aren't decoration, they're active validation.**

> **Route ordering matters.** If you also have `@app.get("/cameras/{camera_id}")` and later add `@app.get("/cameras/summary")`, and `summary` is declared *after* the `{camera_id}` route, a request to `/cameras/summary` will match `{camera_id}` first, with `camera_id="summary"` — because FastAPI matches routes top to bottom, and `{camera_id}` matches anything. Fixed-path routes that could collide with a parameterized one must be declared *above* it.

## Step 10 — Query parameters

Path parameters are for identifying *which resource* (which camera). Query parameters are for everything else — filters, options, pagination. Any function argument that is **not** named in the path template automatically becomes a query parameter:

```python
@app.get("/cameras")
def list_cameras(recording: bool | None = None):
    if recording is None:
        return cameras
    return [c for c in cameras if c["recording"] == recording]
```

Now:

- `/cameras` → all cameras (the `= None` default means the parameter is optional).
- `/cameras?recording=true` → only recording cameras.
- `/cameras?recording=false` → only non-recording ones.

FastAPI converts the text `true`/`false`/`1`/`0` in the URL into a real Python `bool` for you — same type-coercion idea as path parameters. Change the default from `None` to a concrete value (e.g. `recording: bool = True`) and the parameter becomes optional with a fallback instead of optional-and-absent — decide deliberately which behavior you want for each parameter, because callers can tell the difference at `/docs`.

### Multiple query parameters together

```python
@app.get("/search")
def search_cameras(location: str = "", limit: int = 10):
    results = [c for c in cameras if location.lower() in c["location"].lower()]
    return results[:limit]
```

Try `/search?location=garage`, `/search?location=&limit=1`, and plain `/search` (both defaults kick in). Open `/docs` and look at the `GET /search` entry — Swagger UI shows both parameters, their types, and their defaults, generated straight from your function signature. You did not describe this anywhere separately.

## Step 11 — Nested JSON

Real responses are rarely flat. Return a dict containing a list:

```python
@app.get("/status")
def status():
    return {
        "camera_count": len(cameras),
        "recording_now": [c["id"] for c in cameras if c["recording"]],
        "cameras": cameras,
    }
```

FastAPI recurses through nested dicts and lists happily — you don't need to do anything special. This is the shape you'll see again in the real project's `/api/fragments` response: an object containing a list of smaller objects.

## Step 12 — Choosing status codes on purpose

Right now, `get_camera` for an unknown ID returns a body that *says* `"error": "not found"` but ships it with HTTP status `200 OK` — which tells any automated client "everything is fine." Fix it properly:

```python
from fastapi import FastAPI, HTTPException

app = FastAPI()

# ... cameras list unchanged ...

@app.get("/cameras/{camera_id}")
def get_camera(camera_id: str):
    for camera in cameras:
        if camera["id"] == camera_id:
            return camera
    raise HTTPException(status_code=404, detail=f"No camera named '{camera_id}'")
```

`raise HTTPException(...)` short-circuits the function and produces a proper `404` response with a JSON body `{"detail": "..."}`. Try it in `/docs` — the response now correctly shows a 404. This distinction (status code vs. body content) will matter directly in a later lesson: the real project's spec requires `GET /api/hls` to return an actual `404` for an empty time range, not a `200` with an apologetic message inside it. Automated clients and monitoring tools read status codes, not prose.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `{camera_id}` always seems to "win" over a more specific route | Declaration order — put fixed-path routes above parameterized ones with the same prefix. |
| Query parameter shows up as `"true"` (a string) instead of `True` | Type hint on the function argument is missing or wrong — declare it as `bool`. |
| `422` on a request you thought should work | Check `/docs` for the exact expected type; the error body tells you which field and why. |
| 404 route not found vs 404 you raised look identical | They're not — FastAPI's built-in "no matching route" 404 has `{"detail": "Not Found"}`; yours has your custom message. Read the body. |

### Recap

- Path parameters (`{name}` in the decorator) identify *which* resource; they're part of the URL path.
- Query parameters are ordinary function arguments not mentioned in the path; `?key=value` in the URL.
- Type hints on either kind trigger automatic parsing **and** validation — a bad value produces a `422` you never coded by hand.
- A default value (`= None`, `= 10`, ...) makes a query parameter optional.
- `raise HTTPException(status_code=..., detail=...)` is how you deliberately choose a non-200 response.
- `/docs` reflects every parameter, type, and default straight from your code — keep it open while you work.

### Exercises

1. Add `GET /cameras/{camera_id}/toggle` that flips that camera's `recording` value in the in-memory list and returns the updated camera. (No persistence needed — restarting the server should reset it, and that's fine.)
2. Add an optional query parameter `min_id: str = ""` to `/search` that additionally filters results whose `id` is alphabetically `>=` the given value.
3. Make `get_camera` raise a `404` (done above) — now also make `list_cameras` return an **empty list with status 200** (not an error) when no cameras match a filter. Explain in one sentence why these two situations deserve different status codes.
4. Using `/docs`, run each of your endpoints at least once through the "Try it out" button before moving on.

**Next part:** so far your API only ever *reads* data. Part C introduces Pydantic models so your API can safely *accept* data too — the difference between trusting whatever a client sends and validating it first.

---

## Part C — Validating Data with Pydantic


Every route so far has only ever sent data *out*. The real backend you'll build later needs endpoints that accept data *in* — a request to start recording, a request body describing a time range. The moment your code accepts input from the outside world, you need a plan for "what if the client sends garbage?" Pydantic is that plan.
## Step 13 — The problem, without Pydantic

Imagine accepting a new camera registration the "manual" way:

```python
from fastapi import FastAPI, Request

app = FastAPI()


@app.post("/cameras-unsafe")
async def add_camera_unsafe(request: Request):
    data = await request.json()
    name = data["name"]          # KeyError if missing
    location = data["location"]  # KeyError if missing
    return {"id": name, "location": location}
```

This works exactly once — when the client sends exactly the right fields, of exactly the right types, every time. Send `{"name": "cam-03"}` with no `location`, and you get a raw `500 Internal Server Error` with a Python traceback — the worst possible response, because it tells the caller nothing useful and leaks your server's internals. Send `{"name": 42}` and it "works," silently storing an integer where you meant a string. You'd need to hand-write a wall of `if` statements to catch every case. Nobody does this by hand anymore — this is exactly the problem Pydantic solves.

## Step 14 — Define a model

A **Pydantic model** is a class describing the exact shape of a piece of data: field names, types, and which are required.

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class CameraIn(BaseModel):
    name: str
    location: str
    recording: bool = False   # optional — defaults to False if the client omits it


@app.post("/cameras")
def add_camera(camera: CameraIn):
    return {"id": camera.name, "location": camera.location, "recording": camera.recording}
```

Compare this to Step 13: the field name, `request: Request`, is replaced with `camera: CameraIn`. That single change tells FastAPI: *parse the request body as JSON, validate it against `CameraIn`, and give me back a real Python object with real attributes* — `camera.name`, not `data["name"]`.

Run it and test through `/docs` (`POST /cameras` → "Try it out" → edit the example JSON body → Execute). Try these bodies in turn:

```json
{"name": "cam-03", "location": "back yard"}
```
```json
{"name": "cam-04", "location": "roof", "recording": true}
```
```json
{"name": "cam-05"}
```

The first two succeed — the second because `recording` is optional with a default. The third fails with a `422` whose body explains, field by field, what's wrong:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "location"],
      "msg": "Field required",
      ...
    }
  ]
}
```

No code you wrote produced that message. Pydantic generated it from the class definition alone. This is the payoff: you describe the *shape* once, and get parsing, type-coercion, defaults, and error messages for free — the same deal type hints gave you for path parameters in Part B, now applied to entire request bodies.

## Step 15 — Type coercion still applies

Send this body:

```json
{"name": "cam-06", "location": "lobby", "recording": "true"}
```

`"true"` is a *string* in that JSON, but `recording: bool` on the model still accepts it — Pydantic coerces sensible string/number representations to the declared type, exactly like path and query parameters did. Now send `{"name": "cam-06", "location": "lobby", "recording": "definitely"}` — that fails, because `"definitely"` isn't a recognizable boolean. Coercion is forgiving about *format*, not about *meaning*.

## Step 16 — Controlling what goes out: `response_model`

So far you've controlled what comes *in*. You can just as deliberately control what goes *out*:

```python
class CameraOut(BaseModel):
    id: str
    location: str
    recording: bool


@app.post("/cameras", response_model=CameraOut)
def add_camera(camera: CameraIn):
    return {"id": camera.name, "location": camera.location, "recording": camera.recording}
```

Two visible effects:

1. `/docs` now documents the exact response shape for this endpoint, not just the request shape — look at the "Responses" section for `POST /cameras`.
2. If your function's return value ever gains an extra field you didn't mean to expose (say you return the whole internal object and it happens to contain a `secret_key`), `response_model` **strips anything not declared on `CameraOut`** before it reaches the client. This is a real security property, not just documentation — it's the difference between "the response happens to look right" and "the response is guaranteed to look right regardless of what the handler function does internally."

## Step 17 — Nested and nullable fields

Models can contain models, and fields can be genuinely optional (as opposed to "optional with a default"):

```python
from typing import Optional
from pydantic import BaseModel


class Resolution(BaseModel):
    width: int
    height: int


class CameraIn(BaseModel):
    name: str
    location: str
    recording: bool = False
    resolution: Optional[Resolution] = None
    notes: str | None = None
```

`resolution: Optional[Resolution] = None` means: either a full nested object matching `Resolution`, or the JSON value `null`, or the field can be omitted entirely — any of the three is valid, and inside your function `camera.resolution` is either `None` or a real `Resolution` instance with `.width`/`.height` attributes, never a raw dict. Try posting a body with `"resolution": {"width": 1920, "height": 1080}` and inspect what comes back.

`str | None` is the modern equivalent of `Optional[str]` — you'll see both spellings in real code; they mean the same thing.

## Step 18 — Where this is going

The real backend's `GET /api/recording` endpoint you'll build later in this course returns exactly this pattern:

```python
class RecordingStatus(BaseModel):
    running: bool
    managed: bool
    pid: int | None
```

Nothing new — a flat model, one nullable field, used as a `response_model`. You now have every tool needed to write that. Part D has you build a simplified version of it end to end.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `422` even though the JSON "looks right" | Check field *names* exactly — Pydantic won't guess that `"loc"` means `"location"`. Also check the `Content-Type` header is `application/json` (curl needs `-H "Content-Type: application/json"` with `-d`). |
| `response_model` response is missing a field you returned | You didn't declare it on the output model — `response_model` only ever shows declared fields, by design. |
| Nested object rejected even though it "looks like" the right shape | Every required field of the nested model must be present too — nesting doesn't relax validation, it just applies it one level deeper. |
| `Optional[X] = None` field still required | Check you wrote the default (`= None`) — `Optional[X]` alone changes the *type* but does not make the field optional to omit; you need both the `Optional`/`| None` type **and** a default value. |

### Recap

- A `BaseModel` subclass declares the exact shape (fields, types, required-vs-optional) of a piece of JSON.
- Type a request-body parameter as your model; FastAPI parses, validates, and coerces automatically, or returns a `422` explaining exactly what's wrong.
- `response_model` documents *and enforces* the shape of what you send back — extra fields are silently dropped, not leaked.
- `Optional[X] = None` (or `X | None = None`) makes a field genuinely nullable/omittable; a plain default (`= False`) makes it optional with a fallback value.
- Models can nest inside models; validation applies recursively.

### Exercises

1. Add a `PATCH /cameras/{camera_id}` endpoint that accepts a small `CameraUpdate` model (all fields optional) and updates only the fields provided, leaving others unchanged, on a matching camera from the in-memory list.
2. Add a `min_length` constraint so `name` cannot be empty — look up `pydantic.Field` and use `name: str = Field(min_length=1)` — and verify via `/docs` that an empty name now produces a `422`.
3. Deliberately send three different malformed bodies to your `POST /cameras` endpoint (wrong type, missing field, extra field) and, for each, write down what status code and error message you got, and whether it matches what you expected before trying it.
4. Explain in your own words why `response_model` matters even for an endpoint that "obviously" returns the right thing — what changes six months from now when someone else edits the handler function?

**Next part:** Part D is the capstone for this on-ramp — you'll combine everything from Part A and Part B and this lesson into a small in-memory API shaped exactly like the recording-control endpoints of the real project, including the idempotency rule that makes it correct.

---

## Part D — A Mini Recording-Status API


This is where Part A and Part B and Part C stop being separate ideas and become one working thing. It's also a preview: the real Cloud VMS backend you'll build later in this course has an endpoint group that looks almost exactly like what you're about to write —

```
GET  /api/recording        → {"running": bool, "managed": bool, "pid": int | null}
POST /api/recording/start  → same shape
POST /api/recording/stop   → same shape, or 409 if the recording wasn't started by this server
```

— except there it controls a real background process (a video pipeline). Here it controls a fake one: a plain Python variable pretending to be a running process. Everything about the *API shape*, the *validation*, and — most importantly — the *idempotency rule* is identical. Only the "does actual work" part is simplified away, on purpose, so you can focus entirely on the web layer.
## Step 19 — Why state changes the rules

Every route in Part A and Part B and Part C either read from a fixed list or validated an incoming request in isolation. This lesson's routes must additionally remember something *between* requests: is the recording currently running? Who started it?

That's new, and worth pausing on: the real project's spec is explicit that this is **the one place the backend holds state**, precisely because it's unusual and needs to be contained deliberately rather than let spread. You're about to feel why, in miniature.

## Step 20 — Model the state and the response shape

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()


class RecordingStatus(BaseModel):
    running: bool
    managed: bool
    pid: int | None


# Module-level state: simulates "a handle to a running background process."
# In the real project this is a subprocess.Popen object; here it's just a dict.
_state = {"running": False, "managed": False, "pid": None}
_next_pid = 1000  # fake PIDs, just to make each simulated start look distinct
```

Notice `_state` lives at **module level**, outside any function. Every request handler reads and writes the same dict — this is what "the server remembers something" looks like in code. It also means: restart Uvicorn, and the state resets to `{"running": False, "managed": False, "pid": None}`, same as the in-memory `cameras` list in Part B. That's fine for this exercise; the real project's equivalent state (a live process handle) *can't* survive a restart either, for the very same reason — there's nothing to reload it from.

## Step 21 — `GET /api/recording`

```python
@app.get("/api/recording", response_model=RecordingStatus)
def recording_status():
    return _state
```

Nothing new here — a `response_model`-typed read, exactly like Part C. Run it and confirm: `{"running": false, "managed": false, "pid": null}`.

## Step 22 — `POST /api/recording/start`, made idempotent

The naive version:

```python
@app.post("/api/recording/start", response_model=RecordingStatus)
def start_recording_naive():
    global _next_pid
    _state["running"] = True
    _state["managed"] = True
    _state["pid"] = _next_pid
    _next_pid += 1
    return _state
```

Call this endpoint twice in a row through `/docs`. Watch the `pid` change on the second call. That's a bug: you now have a stale reference to a "process" your code has lost track of — in the real project this exact mistake means two publishers writing to the same video stream at once, corrupting the archive. **Starting something that's already started must not start a second one.**

The fix — check state before acting:

```python
@app.post("/api/recording/start", response_model=RecordingStatus)
def start_recording():
    global _next_pid
    if _state["running"]:
        return _state          # already running — report current state, do nothing
    _state["running"] = True
    _state["managed"] = True
    _state["pid"] = _next_pid
    _next_pid += 1
    return _state
```

Call it three times in a row now. The `pid` in the response should be identical every time after the first call. This is what **idempotent** means in practice: calling the operation once, or five times, leaves the system in the same state as calling it once. It's not automatic — you write the `if` check on purpose. Contrast this with `POST /cameras` from Part C: calling that twice with the same body creates two separate resources. Not every `POST` should be idempotent; this particular one must be, because "start" describes a target state ("recording should be on"), not a request to create a new thing.

## Step 23 — `POST /api/recording/stop`, and the `managed` distinction

The real project makes a sharp distinction: a recording this server started (`managed: true`) is this server's to stop. A recording started some other way — someone running the pipeline directly in their own terminal — is *reported* but is **not this server's to kill**. Simulate that distinction:

```python
@app.post("/api/recording/stop", response_model=RecordingStatus)
def stop_recording():
    if not _state["running"]:
        return _state                          # already stopped — idempotent, same idea as start
    if not _state["managed"]:
        raise HTTPException(
            status_code=409,
            detail="Recording is running but was not started by this server; stop it where it was started.",
        )
    _state["running"] = False
    _state["managed"] = False
    _state["pid"] = None
    return _state
```

Two return paths and one error path, each deliberate:

- Not running at all → idempotent no-op, same reasoning as `start`.
- Running **and** managed by this server → actually stop it.
- Running but **not** managed by this server → refuse, with `409 Conflict`. Not `400` (the request itself isn't malformed) and not `404` (the resource exists, it's just not yours to change) — `409` specifically means *the request conflicts with the current state of the resource*, which is exactly this situation.

## Step 24 — Simulating "someone else started it"

There's no real external process here, so give yourself a way to simulate one — a small helper endpoint, clearly marked as test-only:

```python
@app.post("/api/recording/_simulate_external_start")
def simulate_external_start():
    """Test helper only: pretend a recording was started outside this server."""
    _state["running"] = True
    _state["managed"] = False
    _state["pid"] = 99999
    return _state
```

Now walk through the full scenario in `/docs`, in this exact order, checking the response each time:

1. `GET /api/recording` → `running: false`.
2. `POST /api/recording/start` → `running: true, managed: true`, some `pid`.
3. `POST /api/recording/start` again → identical response, same `pid` (idempotency, Step 22).
4. `POST /api/recording/stop` → `running: false, managed: false, pid: null`.
5. `POST /api/recording/_simulate_external_start` → `running: true, managed: false, pid: 99999`.
6. `POST /api/recording/stop` → **409**, with your explanatory message. The state must not change — confirm with a `GET /api/recording` right after.

If step 6 doesn't produce a 409, or if it does but the state changed anyway, you have a real bug — go back to Step 23's code before continuing.

## Step 25 — What's simplified, and what isn't

Be precise about the gap between this and the real thing, so you carry the right lesson forward:

| Here (toy) | Real project (later in this course) |
|---|---|
| `_state` is a plain `dict` | A `subprocess.Popen` handle to an actual GStreamer pipeline |
| "pid" is a fake incrementing counter | A real OS process ID |
| `_simulate_external_start` fakes external ownership | Detected by scanning real `ps` output for a matching command line |
| Stopping just flips a flag | Sends `SIGTERM`, waits up to 15s, escalates to `SIGKILL` if ignored |
| State always resets on restart | Same limitation — this is not a simplification, it's shared with the real system |

Everything in the left column is a stand-in for something with real-world consequences on the right. The **shape of the API, the idempotency rule, and the `managed` conflict logic are not simplified at all** — you just wrote the real design, against fake state. When you build the actual `server/recording.py` later, this lesson's code is structurally what you're extending, not throwing away.

---

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `pid` changes on repeated `start` calls | The idempotency check (`if _state["running"]: return _state`) is missing or placed after the state mutation instead of before it. |
| `stop` after `_simulate_external_start` doesn't 409 | Check `_simulate_external_start` actually sets `managed: False`, and that `stop` checks `managed` before mutating state. |
| State doesn't reset between test runs and confuses you | That's `--reload` preserving the module-level dict across code edits within the same process; a full server restart (`Ctrl+C`, rerun `uvicorn`) resets it. |
| `NameError: global _next_pid` or similar | You need the `global` declaration inside any function that *reassigns* a module-level name (`_next_pid += 1`), not inside functions that only mutate a mutable value in place (`_state["running"] = True` doesn't need it). |

### Recap

- Server-side state (a module-level variable, here; a `Popen` handle, later) is the exception, not the rule — most of what you've built this module is stateless request/response.
- Idempotency for a "start" operation means checking current state before acting, so repeated calls are safe.
- `409 Conflict` is the correct status code for "the request is fine, but it conflicts with the resource's current state" — distinct from `400` (bad request) and `404` (no such resource).
- A `managed`-style ownership flag lets a server distinguish "things I'm responsible for" from "things I merely observe" — and refuse to act on the latter.
- Everything you built here about validation, response shape, and status codes was Part A and Part B and Part C, unchanged; only the addition of state and idempotency was new.

### Exercises

1. Add a fourth field to `RecordingStatus`, `started_at: float | None`, populated with `time.time()` on a real start and cleared on stop. Confirm it survives the idempotent double-`start` case unchanged (it shouldn't reset on the second call).
2. Write a short Python script (using the `requests` library, `pip install requests`) that calls `start`, then `start` again, then `stop`, then `stop` again, printing each response — confirm programmatically what you confirmed by hand in Step 24.
3. Currently `_simulate_external_start` is a real route reachable by anyone. In one or two sentences, explain why a real project would never ship a route like this, and what you'd do instead to test the `managed: false` path safely (hint: think about what a proper *test suite*, run separately from the live server, would do instead).
4. The real spec says stopping should escalate from `SIGTERM` to `SIGKILL` after a 15-second wait. Sketch — in comments, no need to fully implement — how you'd adapt this lesson's `stop_recording` if `_state` held a real `subprocess.Popen` object instead of a dict.

---

### Wrap-up: what the web layer now knows

You now have, in your own hands, working examples of every piece the real Cloud VMS backend is built from: routes that read (Part A), routes that take input safely via path and query parameters (Part B), routes that validate structured input and shape structured output with Pydantic (Part C), and a stateful pair of endpoints with a real idempotency and conflict-handling rule (this lesson) — but `_state["pid"]` here is still fake, and `stop_recording` still can't actually stop anything.

The next lesson closes exactly that gap: Lesson 2 builds a real supervised process — using `subprocess.Popen` and OS signals (`SIGINT`, `SIGTERM`, `SIGKILL`) — that this lesson's `_state` dict was always meant to stand in for. Once that lesson is done, the two halves combine: this API's `start`/`stop` will hold and control a real process handle instead of a dict.
