# server/fixtures.py — Lesson 6, Step 7. Development aid. Never enabled in a
# real run: the flag is off by default, and this is the one place to look
# when the timeline shows footage you cannot explain.
import os


def enabled():
    return os.getenv("VMS_FIXTURES") == "1"


def runs(window_start, window_end):
    """Four runs with three obvious gaps, positioned relative to 'now'
    so the timeline looks alive whenever you reload."""
    spans = [(-58 * 60, -44 * 60), (-42 * 60, -28.5 * 60),
             (-21 * 60, -9 * 60), (-6 * 60, -20)]
    return [{"start": window_end + a, "end": window_end + b} for a, b in spans]
