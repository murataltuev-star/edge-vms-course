# Lesson 2 — Routes, Path Parameters, and Query Parameters

**Module:** Web Basics for the Cloud VMS Project
**You will build:** an API with several routes that accept input from the URL.
**Time:** ~45–60 minutes.

## Why this lesson exists

The backend you'll build later in this course has an endpoint like `GET /api/fragments?start=1756382400&end=1756386000` — a fixed path with variable values attached. Right now your routes take zero input. This lesson closes that gap: how does a value typed into a URL end up as a properly-typed Python variable inside your function?

## Prerequisites

- Lesson 1 completed: you can create, run, and query a FastAPI app.

## Learning objectives

1. Distinguish path parameters from query parameters and know when to use each.
2. Declare typed parameters and get automatic validation for free.
3. Make query parameters optional, with defaults.
4. Return lists and nested JSON structures, not just flat dicts.
5. Understand and deliberately choose HTTP status codes.

---

## Step 1 — Start from a fresh app

Continue in the same `fastapi-intro` project from Lesson 1 (same virtual environment, activated). Replace the contents of `main.py`:

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

## Step 2 — Path parameters

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

Try `http://127.0.0.1:8000/cameras/cam-01` and `http://127.0.0.1:8000/cameras/cam-99`. The second one hits your `"not found"` branch — but returns it with a `200 OK` status, which is misleading. You'll fix that in Step 5.

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

## Step 3 — Query parameters

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

## Step 4 — Nested JSON

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

## Step 5 — Choosing status codes on purpose

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

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `{camera_id}` always seems to "win" over a more specific route | Declaration order — put fixed-path routes above parameterized ones with the same prefix. |
| Query parameter shows up as `"true"` (a string) instead of `True` | Type hint on the function argument is missing or wrong — declare it as `bool`. |
| `422` on a request you thought should work | Check `/docs` for the exact expected type; the error body tells you which field and why. |
| 404 route not found vs 404 you raised look identical | They're not — FastAPI's built-in "no matching route" 404 has `{"detail": "Not Found"}`; yours has your custom message. Read the body. |

## Recap

- Path parameters (`{name}` in the decorator) identify *which* resource; they're part of the URL path.
- Query parameters are ordinary function arguments not mentioned in the path; `?key=value` in the URL.
- Type hints on either kind trigger automatic parsing **and** validation — a bad value produces a `422` you never coded by hand.
- A default value (`= None`, `= 10`, ...) makes a query parameter optional.
- `raise HTTPException(status_code=..., detail=...)` is how you deliberately choose a non-200 response.
- `/docs` reflects every parameter, type, and default straight from your code — keep it open while you work.

## Exercises

1. Add `GET /cameras/{camera_id}/toggle` that flips that camera's `recording` value in the in-memory list and returns the updated camera. (No persistence needed — restarting the server should reset it, and that's fine.)
2. Add an optional query parameter `min_id: str = ""` to `/search` that additionally filters results whose `id` is alphabetically `>=` the given value.
3. Make `get_camera` raise a `404` (done above) — now also make `list_cameras` return an **empty list with status 200** (not an error) when no cameras match a filter. Explain in one sentence why these two situations deserve different status codes.
4. Using `/docs`, run each of your endpoints at least once through the "Try it out" button before moving on.

**Next lesson:** so far your API only ever *reads* data. Lesson 3 introduces Pydantic models so your API can safely *accept* data too — the difference between trusting whatever a client sends and validating it first.
