"""Lesson 5 — pagination to exhaustion, the merge rule, the timestamp boundary."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.fragments import list_all_fragments, merge_fragments_into_runs
from server.models import from_epoch, to_epoch


class FakePaginatingClient:
    """Serves 3 fixed pages, and asserts it's called the way the real API requires."""
    def __init__(self):
        self.pages = [
            {"Fragments": ["f1", "f2"], "NextToken": "tok-a"},
            {"Fragments": ["f3"], "NextToken": "tok-b"},
            {"Fragments": ["f4", "f5"]},          # no NextToken: last page
        ]
        self.call_count = 0

    def list_fragments(self, **kwargs):
        page = self.pages[self.call_count]
        if self.call_count == 0:
            assert "FragmentSelector" in kwargs, "first call must include FragmentSelector"
        else:
            assert "NextToken" in kwargs and "FragmentSelector" not in kwargs, \
                "resumed calls must use NextToken alone"
        self.call_count += 1
        return page


def test_pagination_to_exhaustion():
    fake = FakePaginatingClient()
    result = list_all_fragments(fake, "cam-01", "start", "end")
    assert result == ["f1", "f2", "f3", "f4", "f5"] and fake.call_count == 3


def test_merge_rule_all_three_branches():
    fragments = [
        {"producer_timestamp": 1000.0, "duration": 10.0},   # 1000-1010
        {"producer_timestamp": 1010.4, "duration": 10.0},   # gap 0.4 -> merges
        {"producer_timestamp": 1025.0, "duration": 10.0},   # gap 4.6 -> new run
        {"producer_timestamp": 1036.0, "duration": 5.0},    # gap exactly 1.0 -> merges
    ]
    assert merge_fragments_into_runs(fragments) == [
        {"start": 1000.0, "end": 1020.4}, {"start": 1025.0, "end": 1041.0}]
    assert merge_fragments_into_runs([]) == []              # an empty archive is not an error


def test_timestamp_boundary_round_trips():
    original = 1756382400.0
    dt = from_epoch(original)
    assert dt.tzinfo is not None
    assert to_epoch(dt) == original
