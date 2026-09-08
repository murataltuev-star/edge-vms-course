# server/fragments.py — Lesson 12, Steps 2–3: pagination to exhaustion, and
# the merging rule. Pure functions over an injected client, so both are
# verified with fakes (tests/test_fragments.py) rather than a live stream.
GAP_SECONDS = 1.0


def list_all_fragments(client, stream_name, start_dt, end_dt):
    """Call list_fragments repeatedly until NextToken is exhausted,
    returning every fragment dict boto3 gave back, unmerged and unsorted.
    The first call carries the FragmentSelector; resumed calls carry
    NextToken ALONE."""
    fragments = []
    kwargs = {
        "StreamName": stream_name,
        "FragmentSelector": {
            "FragmentSelectorType": "PRODUCER_TIMESTAMP",
            "TimestampRange": {"StartTimestamp": start_dt, "EndTimestamp": end_dt},
        },
    }
    while True:
        resp = client.list_fragments(**kwargs)
        fragments.extend(resp["Fragments"])
        next_token = resp.get("NextToken")
        if not next_token:
            break
        kwargs = {"StreamName": stream_name, "NextToken": next_token}
    return fragments


def merge_fragments_into_runs(fragments):
    """fragments: list of {"producer_timestamp": float, "duration": float},
    already sorted ascending by producer_timestamp.
    Contiguous when next.start - prev.end <= 1.0 s (<=, not <)."""
    runs = []
    for frag in fragments:
        start = frag["producer_timestamp"]
        end = start + frag["duration"]
        if runs and (start - runs[-1]["end"]) <= GAP_SECONDS:
            runs[-1]["end"] = end
        else:
            runs.append({"start": start, "end": end})
    return runs
