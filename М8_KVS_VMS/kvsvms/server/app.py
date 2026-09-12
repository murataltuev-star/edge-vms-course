# server/app.py — Lesson 6, Step 6: everything in one process.
# API routes are declared BEFORE the static mount, or the mount swallows them.
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from botocore.exceptions import ClientError

from server import fixtures, recording
from server.config import PLAYBACK_CHUNK_SECONDS, STREAM_NAME
from server.fragments import list_all_fragments, merge_fragments_into_runs
from server.kvs import archived_client
from server.models import (FragmentsResponse, HLSResponse, RecordingState, Run, Window,
                           from_epoch, to_epoch)
import os

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

app = FastAPI(title="Cloud VMS")


# ---- archive (Lesson 5) --------------------------------------------------

@app.get("/api/fragments", response_model=FragmentsResponse)
def get_fragments(start: float, end: float):
    if fixtures.enabled():
        return FragmentsResponse(runs=[Run(**r) for r in fixtures.runs(start, end)],
                                 window=Window(start=start, end=end))
    client = archived_client("LIST_FRAGMENTS")
    raw = list_all_fragments(client, STREAM_NAME, from_epoch(start), from_epoch(end))
    fragments = sorted(
        ({"producer_timestamp": to_epoch(f["ProducerTimestamp"]),
          "duration": f["FragmentLengthInMilliseconds"] / 1000.0} for f in raw),
        key=lambda f: f["producer_timestamp"],
    )
    runs = merge_fragments_into_runs(fragments)
    return FragmentsResponse(runs=[Run(**r) for r in runs], window=Window(start=start, end=end))


@app.get("/api/hls", response_model=HLSResponse)
def get_hls(start: float, end: float):
    # Cheap check first, and a different KIND of wrong from "no footage".
    duration = end - start
    if not (0 < duration <= PLAYBACK_CHUNK_SECONDS):
        raise HTTPException(400, f"range must be greater than 0 and at most "
                                 f"{PLAYBACK_CHUNK_SECONDS} seconds")
    try:
        resp = archived_client("GET_HLS_STREAMING_SESSION_URL").get_hls_streaming_session_url(
            StreamName=STREAM_NAME,
            PlaybackMode="ON_DEMAND",
            HLSFragmentSelector={
                "FragmentSelectorType": "PRODUCER_TIMESTAMP",
                "TimestampRange": {"StartTimestamp": from_epoch(start),
                                   "EndTimestamp": from_epoch(end)},
            },
            Expires=300,                        # AWS's documented minimum; fresh URL per seek
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise HTTPException(404, "No recording in this range")
        raise
    return HLSResponse(url=resp["HLSStreamingSessionURL"])


# ---- recording control (Lesson 2, now aimed at the real pipeline) ----------

@app.get("/api/recording", response_model=RecordingState)
def recording_status():
    return recording.status()


@app.post("/api/recording/start", response_model=RecordingState)
def recording_start():
    return recording.start()


@app.post("/api/recording/stop", response_model=RecordingState)
def recording_stop():
    try:
        return recording.stop()
    except recording.NotManaged as e:
        raise HTTPException(409, str(e))


# ---- М9 Lesson 3: the health check's row 2 --------------------------------
# "The VMS answers." Nothing more: whether footage is being written is row 3,
# and it is answered by the spool (М9) or the Node (М9), never by this route.

@app.get("/health")
def health():
    return {"ok": True, "recording": recording.status()}


# ---- static frontend (must be mounted LAST) -------------------------------

class NoCacheStatic(StaticFiles):
    """Serve web/ with revalidation. There is no build step and no hashed
    filenames, so heuristic browser caching silently serves stale app.js."""
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/", NoCacheStatic(directory=WEB_DIR, html=True), name="web")
