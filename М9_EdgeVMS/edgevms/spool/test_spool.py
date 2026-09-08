"""Lesson 19, Step 7 — the spool is ordinary code; test it like ordinary code."""
import os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spool as S

root = os.path.join(tempfile.mkdtemp(), "data")
seg = b"x" * 100

# 1. delete only on acknowledgement
sp = S.Spool(root, max_bytes=10_000)
for i in range(5): sp.accept(f"2026-09-06T10-0{i}-00.mp4", seg)
link_up = False
assert S.drain(sp, lambda p: link_up) == 0 and len(sp.pending()) == 5, "failed upload must keep the file"
link_up = True
S.drain(sp, lambda p: link_up, budget_per_tick=99)
assert sp.pending() == [], "acknowledged uploads must be removed"
print("1. delete-on-ack .......... OK")

# 2. bound + drop-oldest: 300 bytes holds THREE 100-byte segments
sp = S.Spool(root, max_bytes=300, policy="drop-oldest")
for i in range(5): assert sp.accept(f"2026-09-06T11-0{i}-00.mp4", seg) is True
names = [os.path.basename(p) for p in sp.pending()]
assert names == ["2026-09-06T11-02-00.mp4", "2026-09-06T11-03-00.mp4", "2026-09-06T11-04-00.mp4"], names
assert sp.dropped == 2
print(f"2. bound, drop-oldest ..... OK (kept {len(names)}, dropped {sp.dropped})")

# 3. bound + stop-recording refuses instead of dropping
shutil.rmtree(root); sp = S.Spool(root, max_bytes=300, policy="stop-recording")
accepted = [sp.accept(f"2026-09-06T12-0{i}-00.mp4", seg) for i in range(5)]
assert accepted == [True, True, True, False, False] and sp.dropped == 0
print(f"3. bound, stop-recording .. OK (accepted {accepted.count(True)}, refused {accepted.count(False)})")

# 4. rate-limited drain: 10 segments at 3 per tick
shutil.rmtree(root); sp = S.Spool(root, max_bytes=10_000)
for i in range(10): sp.accept(f"2026-09-06T13-{i:02d}-00.mp4", seg)
ticks = 0
while sp.pending():
    S.drain(sp, lambda p: True, budget_per_tick=3); ticks += 1
assert ticks == 4
print(f"4. rate-limited drain ..... OK (10 segments, budget 3 -> {ticks} ticks)")

# 5. oldest first, across per-camera directories, stopping at the first failure
shutil.rmtree(root); sp = S.Spool(root, max_bytes=10_000)
for cam, i in [("cam1", 3), ("cam2", 1), ("cam1", 4), ("cam2", 0), ("cam1", 2)]:
    os.makedirs(os.path.join(root, cam), exist_ok=True)
    with open(os.path.join(root, cam, f"2026-09-06T14-0{i}-00.mp4"), "wb") as f: f.write(seg)
order = []
S.drain(sp, lambda p: (order.append(os.path.basename(p)), True)[1], budget_per_tick=99)
assert order == sorted(order), order
print(f"5. oldest-first ordering .. OK ({order[0]} .. {order[-1]})")

# 6. the two numbers (Step 9): age of the oldest unsent segment, and fullness
sp = S.Spool(root, max_bytes=1_000)
sp.accept("2026-09-06T15-00-00.mp4", seg)
os.utime(sp.pending()[0], (time.time() - 3600, time.time() - 3600))
sp.accept("2026-09-06T15-10-00.mp4", seg)
sig = sp.signals()
assert 3590 < sig["spool_oldest_seconds"] < 3610 and sig["spool_bytes_used"] == 200 and sig["spool_bytes_bound"] == 1000
p = os.path.join(root, "run", "spool-signals"); S.write_signals(p, sig)
assert open(p).read().startswith("spool_oldest_seconds ")
print(f"6. two signals ............ OK (oldest {sig['spool_oldest_seconds']:.0f}s, {sig['spool_bytes_used']}/{sig['spool_bytes_bound']} bytes)")
print("\nall 6 pass")
