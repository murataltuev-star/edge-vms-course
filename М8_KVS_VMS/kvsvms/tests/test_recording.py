"""Lesson 6, Step 3 — detect a process by POSITION, not presence."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import recording

PS = """  PID ARGS
  100 vim edge/looper.py
   99 less looper.py
  101 tail -f looper.py
  102 /usr/bin/python3 /srv/kvsvms/edge/looper.py
  103 python3 ./looper.py
  104 grep looper.py
"""


def test_positional_match_skips_editors_and_grep():
    assert recording._scan_ps(PS, own_pid=None) == 102


def test_own_process_is_not_reported_as_external():
    assert recording._scan_ps(PS, own_pid=102) == 103


def test_nothing_running():
    assert recording._scan_ps("  PID ARGS\n  1 vim looper.py\n", own_pid=None) is None
