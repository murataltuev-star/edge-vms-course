# Lesson 3 — Validating Data with Pydantic

**Module:** Web Basics for the Cloud VMS Project
**You will build:** an API that safely accepts data from clients, not just serves it.
**Time:** ~60–75 minutes.

## Why this lesson exists

Every route so far has only ever sent data *out*. The real backend you'll build later needs endpoints that accept data *in* — a request to start recording, a request body describing a time range. The moment your code accepts input from the outside world, you need a plan for "what if the client sends garbage?" Pydantic is that plan.

## Prerequisites

- Lessons 1–2 completed.

## Learning objectives

1. Explain what Pydantic does and why FastAPI is built around it.
2. Define a `BaseModel` describing the shape of expected data.
3. Accept a validated JSON body with `POST`.
4. Read and act on FastAPI's automatic `422` validation errors.
5. Use `response_model` to guarantee the *shape* of what your API sends back, not just what it accepts.
6. Use `Optional` fields, defaults, and nested models.

---

## Step 1 — The problem, without Pydantic

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

## Step 2 — Define a model

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

Compare this to Step 1: the field name, `request: Request`, is replaced with `camera: CameraIn`. That single change tells FastAPI: *parse the request body as JSON, validate it against `CameraIn`, and give me back a real Python object with real attributes* — `camera.name`, not `data["name"]`.

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

No code you wrote produced that message. Pydantic generated it from the class definition alone. This is the payoff: you describe the *shape* once, and get parsing, type-coercion, defaults, and error messages for free — the same deal type hints gave you for path parameters in Lesson 2, now applied to entire request bodies.

## Step 3 — Type coercion still applies

Send this body:

```json
{"name": "cam-06", "location": "lobby", "recording": "true"}
```

`"true"` is a *string* in that JSON, but `recording: bool` on the model still accepts it — Pydantic coerces sensible string/number representations to the declared type, exactly like path and query parameters did. Now send `{"name": "cam-06", "location": "lobby", "recording": "definitely"}` — that fails, because `"definitely"` isn't a recognizable boolean. Coercion is forgiving about *format*, not about *meaning*.

## Step 4 — Controlling what goes out: `response_model`

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

## Step 5 — Nested and nullable fields

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

## Step 6 — Where this is going

The real backend's `GET /api/recording` endpoint you'll build later in this course returns exactly this pattern:

```python
class RecordingStatus(BaseModel):
    running: bool
    managed: bool
    pid: int | None
```

Nothing new — a flat model, one nullable field, used as a `response_model`. You now have every tool needed to write that. Lesson 4 has you build a simplified version of it end to end.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `422` even though the JSON "looks right" | Check field *names* exactly — Pydantic won't guess that `"loc"` means `"location"`. Also check the `Content-Type` header is `application/json` (curl needs `-H "Content-Type: application/json"` with `-d`). |
| `response_model` response is missing a field you returned | You didn't declare it on the output model — `response_model` only ever shows declared fields, by design. |
| Nested object rejected even though it "looks like" the right shape | Every required field of the nested model must be present too — nesting doesn't relax validation, it just applies it one level deeper. |
| `Optional[X] = None` field still required | Check you wrote the default (`= None`) — `Optional[X]` alone changes the *type* but does not make the field optional to omit; you need both the `Optional`/`| None` type **and** a default value. |

## Recap

- A `BaseModel` subclass declares the exact shape (fields, types, required-vs-optional) of a piece of JSON.
- Type a request-body parameter as your model; FastAPI parses, validates, and coerces automatically, or returns a `422` explaining exactly what's wrong.
- `response_model` documents *and enforces* the shape of what you send back — extra fields are silently dropped, not leaked.
- `Optional[X] = None` (or `X | None = None`) makes a field genuinely nullable/omittable; a plain default (`= False`) makes it optional with a fallback value.
- Models can nest inside models; validation applies recursively.

## Exercises

1. Add a `PATCH /cameras/{camera_id}` endpoint that accepts a small `CameraUpdate` model (all fields optional) and updates only the fields provided, leaving others unchanged, on a matching camera from the in-memory list.
2. Add a `min_length` constraint so `name` cannot be empty — look up `pydantic.Field` and use `name: str = Field(min_length=1)` — and verify via `/docs` that an empty name now produces a `422`.
3. Deliberately send three different malformed bodies to your `POST /cameras` endpoint (wrong type, missing field, extra field) and, for each, write down what status code and error message you got, and whether it matches what you expected before trying it.
4. Explain in your own words why `response_model` matters even for an endpoint that "obviously" returns the right thing — what changes six months from now when someone else edits the handler function?

**Next lesson:** Lesson 4 is the capstone for this on-ramp — you'll combine everything from Lessons 1–3 into a small in-memory API shaped exactly like the recording-control endpoints of the real project, including the idempotency rule that makes it correct.
