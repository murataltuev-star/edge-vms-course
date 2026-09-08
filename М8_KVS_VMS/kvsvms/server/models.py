# server/models.py — Lesson 12, Step 1: the timestamp boundary.
# boto3 speaks datetime; this API speaks Unix-epoch floats. The conversion
# happens here and nowhere else.
from datetime import datetime, timezone
from pydantic import BaseModel


def to_epoch(dt: datetime) -> float:
    """boto3 datetime -> the float this API always sends."""
    return dt.timestamp()


def from_epoch(ts: float) -> datetime:
    """The float this API always receives -> a boto3-compatible datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class Run(BaseModel):
    start: float
    end: float


class Window(BaseModel):
    start: float
    end: float


class FragmentsResponse(BaseModel):
    runs: list[Run]
    window: Window


class HLSResponse(BaseModel):
    url: str


class RecordingState(BaseModel):
    running: bool
    managed: bool
    pid: int | None
