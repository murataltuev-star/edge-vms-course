# Lesson 1 — Your First FastAPI App

**Module:** Web Basics for the Cloud VMS Project
**You will build:** a one-file web server that answers HTTP requests, running on your own machine.
**Time:** ~45–60 minutes.

## Why this lesson exists

Later in this course you will build the backend for a real video surveillance system — the thing that lets a browser ask "what footage exists?" and "let me watch this moment." That backend is a **web server**. Before touching any of that, you need to be able to answer a much smaller question: *what actually happens between a browser and a piece of Python code?*

This lesson answers that question by building the smallest possible web app, three times, each time understanding one more layer of what's going on.

## Prerequisites

- Python 3.11 or newer installed (`python3 --version` to check).
- Comfortable with basic Python: functions, running scripts from a terminal.
- No prior web development experience assumed.

## Learning objectives

By the end of this lesson you will be able to:

1. Explain, in your own words, what a web server does when a browser "visits" it.
2. Set up an isolated Python environment for a project.
3. Install and run **FastAPI** (the web framework) and **Uvicorn** (the server that runs it).
4. Write and run a minimal FastAPI application with a single route.
5. Use FastAPI's automatic interactive documentation to test your API without writing a browser UI.

---

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

That's the entire mental model. Everything in this module is a variation on "receive a request, do something, send a response."

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

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `command not found: uvicorn` | Virtual environment not activated, or install failed — re-run Step 1 then Step 2. |
| `ModuleNotFoundError: No module named 'fastapi'` | Same as above — you're running a Python outside the venv. |
| `Address already in use` on startup | Something else is already listening on port 8000. Stop it, or run `uvicorn main:app --reload --port 8001`. |
| Browser shows `{"detail":"Not Found"}` | You requested a path with no matching route — check for typos, and remember paths are case-sensitive. |
| Editing `main.py` does nothing | You forgot `--reload`, or you're editing a different copy of the file than the one you ran Uvicorn from. |

## Recap

- A web app is a client and a server exchanging HTTP requests and responses; the server is stateless by default.
- Virtual environments keep each project's dependencies isolated.
- FastAPI describes *what* happens for each route; Uvicorn is the server that actually *runs* it and listens on the network.
- `@app.get(path)` registers a handler function for that path.
- Returning a `dict` is enough — FastAPI serializes it to JSON for you.
- `/docs` gives you a working test UI for free, generated from your code.

## Exercises

1. Add a third route, `GET /about`, that returns your name and one fact about you as JSON.
2. Change the message returned by `/` and confirm (via the still-running `--reload` server) that you see the change without restarting Uvicorn yourself.
3. Stop the server (`Ctrl+C`) and start it again, this time on port `9000`. Confirm `/docs` works at the new port.
4. In your own words (2–3 sentences, no looking back at this document), explain to a partner or write down: what job does FastAPI do, and what job does Uvicorn do?

**Next lesson:** routes get more useful once they can take input. Lesson 2 covers path and query parameters — how `/items/42?verbose=true` gets turned into typed Python values automatically.
