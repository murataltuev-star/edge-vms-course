"""Lesson 2 — shadow mode: the taxonomy, slow versus stuck, the one number."""
from domain.shadow import NodeReport, Shadow, exit_criterion


def rep(node, cams, obs, epoch=1, cluster="north"):
    return NodeReport(node, cluster, epoch, cams, obs, 0.0)


def test_the_taxonomy():
    sh = Shadow(grace_seconds=60)
    placed = {1: "node-1", 2: "node-1", 3: "node-2", 9: "node-3"}
    epochs = {"node-1": 1, "node-2": 1, "node-3": 2, "node-4": 1}
    reports = [rep("node-1", [1, 2], 5), rep("node-2", [3, 4], 5),        # 4 is unmanaged
               rep("node-3", [9], 5, epoch=1),                            # stale epoch: fenced writer still reporting
               rep("node-4", [3], 5)]                                     # conflict with node-2 over 3
    r = sh.compare(placed, epochs, revision=5, reports=reports, now=100.0)
    assert r.count("unmanaged") == 1 and r.count("conflict") == 1 and r.count("stale_epoch") == 1
    assert r.count("orphaned") == 1                                       # 9: its only claimant was fenced
    assert r.count("lagging") == 0 and r.count("stalled") == 0
    assert "unmanaged=1" in r.summary()


def test_slow_versus_stuck():
    sh = Shadow(grace_seconds=60)
    placed, epochs = {1: "node-1"}, {"node-1": 1}
    r = sh.compare(placed, epochs, 7, [rep("node-1", [1], 5)], now=100.0)
    assert r.count("lagging") == 1 and r.findings[0].detail.startswith("behind by 2")     # distance, not "diverged"
    r = sh.compare(placed, epochs, 7, [rep("node-1", [1], 5)], now=200.0)
    assert r.count("stalled") == 1 and "for 100s" in r.findings[0].detail                # past grace, no progress
    r = sh.compare(placed, epochs, 7, [rep("node-1", [1], 6)], now=210.0)
    assert r.count("lagging") == 1                                                       # progress moved: slow again
    r = sh.compare(placed, epochs, 7, [rep("node-1", [1], 7)], now=220.0)
    assert not r.findings


def test_exit_criterion_is_written_down():
    sh = Shadow()
    dirty = sh.compare({1: "node-1"}, {"node-1": 1}, 1, [rep("node-1", [1, 2], 1)], now=0)
    ok, why = exit_criterion(dirty, consecutive_clean=10)
    assert not ok and "unmanaged=1" in why
    clean = sh.compare({1: "node-1", 2: "node-1"}, {"node-1": 1}, 1, [rep("node-1", [1, 2], 1)], now=0)
    assert exit_criterion(clean, 2) == (False, "2/3 consecutive clean reports")
    assert exit_criterion(clean, 3)[0]
