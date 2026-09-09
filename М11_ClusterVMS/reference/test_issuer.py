"""Lesson 4, Step 4 — the wrong answer first, then the right one."""
import threading, time, uuid
from variables import Conflict, VariableLock, Variables, next_epoch

# 1. The variable lock: the IDs carry no order at all.
lock = VariableLock(ttl=0.05)
a = lock.acquire(); time.sleep(0.06); b = lock.acquire()      # a expired; b is a new holder
assert a and b and a != b
assert lock.renew(a) is False                                  # the old holder is refused...
# ...but nothing downstream can tell which of a or b is NEWER: they are UUIDs.
try:
    order = uuid.UUID(a) < uuid.UUID(b)
    print(f"1. variable lock ............ two lock IDs, no order: {a[:8]} vs {b[:8]} "
          f"({'a<b' if order else 'b<a'} as strings — meaningless)")
except Exception:
    raise

# 2. CAS: two racing issuers, one epoch each, strictly increasing, no reuse.
v = Variables()
issued = []
def race():
    for _ in range(50):
        issued.append(next_epoch(v, "node-3")[0])
ts = [threading.Thread(target=race) for _ in range(4)]
[t.start() for t in ts]; [t.join() for t in ts]
assert sorted(issued) == list(range(1, 201)), "every epoch exactly once"
print(f"2. CAS issuer ............... 4 threads x 50 = {len(issued)} epochs, all distinct, 1..{max(issued)}")

# 3. A stale writer with an old index gets 409, never a silent overwrite.
items, idx = v.get("nodes/node-3/epoch")
v.put("nodes/node-3/epoch", {"epoch": "999"}, cas=idx)        # somebody else moved it
try:
    v.put("nodes/node-3/epoch", {"epoch": "1000"}, cas=idx)
    raise AssertionError("stale cas must not succeed")
except Conflict as e:
    print(f"3. stale cas ................ 409 ({e})")

# 4. ModifyIndex alone is a usable epoch: raft-assigned, monotonic, never reused.
idxs = [v.put("nodes/node-3/heartbeat", {"t": str(i)}) for i in range(5)]
assert idxs == sorted(idxs) and len(set(idxs)) == 5
print(f"4. ModifyIndex as epoch ..... {idxs} — increase is all fencing needs")
print("\nall 4 pass")
