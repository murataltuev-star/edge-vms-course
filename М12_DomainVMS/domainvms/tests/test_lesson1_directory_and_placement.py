"""Lesson 1 — what a cluster cannot know: lookup across clusters,
incompleteness as a result, placement by reachability, CAS against two
placers, a dead cluster is not a trigger."""
import threading
from domain.federation import DomainDirectory
from domain.placement import CameraSite, ClusterPlacer, Refused
from tests.conftest import make_domain, publish_node


async def test_where_across_three_clusters():
    fed, links = make_domain({"north": ("vlan:a",), "south": ("vlan:b",), "cloud": ("vlan:c",)}, "north")
    await publish_node(fed.clusters["north"], "node-1", [1, 2])
    await publish_node(fed.clusters["south"], "node-4", [7])
    await publish_node(fed.clusters["cloud"], "node-9", [50])
    d = DomainDirectory(fed)
    a = d.where(7)
    assert a.found and a.node == "node-4" and a.cluster == "south" and a.complete
    assert d.where(50).cluster == "cloud" and d.where(1).cluster == "north"
    none = d.where(99)
    assert not none.found and none.complete and "3 clusters searched" in none.sentence()


async def test_unreachable_cluster_makes_the_answer_incomplete_not_short():
    fed, links = make_domain({"north": ("vlan:a",), "south": ("vlan:b",)}, "north")
    await publish_node(fed.clusters["north"], "node-1", [1])
    await publish_node(fed.clusters["south"], "node-4", [7])
    links["south"].up = False
    a = DomainDirectory(fed).where(7)
    assert not a.found and not a.complete and a.unreachable == ["south"] and a.searched == ["north"]
    assert "not 'not anywhere'" in a.sentence()               # never a short list read as complete
    b = DomainDirectory(fed).where(1)
    assert b.found and not b.complete and "could not be asked" in b.sentence()


async def test_two_clusters_claiming_a_camera_is_a_fault_not_a_tie():
    fed, _ = make_domain({"north": (), "south": ()}, "north")
    await publish_node(fed.clusters["north"], "node-1", [7])
    await publish_node(fed.clusters["south"], "node-4", [7])
    try:
        DomainDirectory(fed).where(7); raise AssertionError("must raise")
    except RuntimeError as e:
        assert "placement or fencing failure" in str(e)


def test_placement_is_by_reachability_then_headroom():
    fed, _ = make_domain({"north": ("vlan:a", "vlan:b"), "south": ("vlan:b",), "cloud": ("vlan:c",)}, "north")
    head = {"north": 10.0, "south": 40.0, "cloud": 99.0}
    p = ClusterPlacer(fed, headroom=lambda c: head[c], clock=lambda: 1234.0)
    a = p.place(CameraSite(1, "vlan:a"))
    assert a.cluster == "north" and "only cluster reaching vlan:a" in a.reason        # capacity elsewhere is irrelevant
    b = p.place(CameraSite(2, "vlan:b"))
    assert b.cluster == "south" and "most headroom" in b.reason                       # ties broken by headroom
    try:
        p.place(CameraSite(3, "vlan:z")); raise AssertionError("must refuse")
    except Refused as e:
        assert "no cluster in the domain reaches vlan:z" in str(e)                    # never "cluster X is full"
    stored, _ = fed.domain_cluster.vars.get("domain/placement/2")
    assert stored["cluster"] == "south" and stored["at"] == "1234.0" and stored["reason"]   # stored, with a reason and a time
    assert p.place(CameraSite(2, "vlan:b")).cluster == "south"                        # placing again changes nothing


def test_two_placers_racing_agree_by_cas():
    fed, _ = make_domain({"north": ("vlan:a",), "south": ("vlan:a",)}, "north")
    results = []
    def race(seed):
        head = {"north": 10.0 + seed, "south": 10.0 + (1 - seed)}      # each placer would pick a different cluster
        p = ClusterPlacer(fed, headroom=lambda c: head[c])
        for cam in range(1, 41):
            results.append((cam, p.place(CameraSite(cam, "vlan:a")).cluster))
    ts = [threading.Thread(target=race, args=(i,)) for i in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    by_cam = {}
    for cam, cl in results:
        by_cam.setdefault(cam, set()).add(cl)
    assert all(len(s) == 1 for s in by_cam.values())               # one camera, one cluster, whoever won
    assert len(by_cam) == 40


def test_a_dead_cluster_is_not_a_trigger():
    fed, links = make_domain({"north": ("vlan:a",), "south": ("vlan:b",)}, "north")
    p = ClusterPlacer(fed)
    assert p.place(CameraSite(7, "vlan:b")).cluster == "south"
    links["south"].up = False
    assert p.place(CameraSite(7, "vlan:b")).cluster == "south"     # already placed: untouched, not re-placed
    try:
        p.place(CameraSite(8, "vlan:b"), unreachable={"south"}); raise AssertionError("must refuse")
    except Refused as e:
        assert "not placing elsewhere" in str(e)                    # nothing else can see it
    try:
        p.rebalance_across_clusters(); raise AssertionError("must refuse")
    except NotImplementedError as e:
        assert "never crosses a cluster" in str(e)


def test_exactly_one_domain_cluster_is_designated():
    fed, _ = make_domain({"north": (), "south": ()}, "nowhere")
    try:
        fed.domain_cluster; raise AssertionError("must raise")
    except RuntimeError as e:
        assert "exactly one domain cluster" in str(e)
