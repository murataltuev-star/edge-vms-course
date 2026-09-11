"""Lesson 3 — the camera list from snapshots; staleness shown; one cause per
dead server; the API refuses placement and is idempotent; the gateway
fans out and the Node's viewer count stays zero."""
import json
import urllib.request
from domain.api import ApiError, ConsoleAPI
from domain.console import Console
from domain.federation import DomainDirectory
from domain.gateway import Forbidden, Gateway, LiveTee, NodeLiveEndpoint
from domain.readview import ReadView
from tests.conftest import Clock, heartbeat, make_domain, publish_node


async def _four_nodes():
    fed, links = make_domain({"north": (), "south": ()}, "north")
    n, s = fed.clusters["north"], fed.clusters["south"]
    await publish_node(n, "node-1", list(range(1, 51)));  await publish_node(n, "node-2", list(range(51, 101)))
    await publish_node(n, "node-3", list(range(101, 151))); await publish_node(s, "node-4", list(range(151, 201)))
    return fed, links


async def test_two_hundred_cameras_from_snapshots_no_node_called():
    fed, links = await _four_nodes()
    wall = Clock(10_000.0)
    for node, cl, srv, cams in (("node-1", "north", "srv-1", range(1, 51)), ("node-2", "north", "srv-1", range(51, 101)),
                                ("node-3", "north", "srv-2", range(101, 151)), ("node-4", "south", "srv-9", range(151, 201))):
        heartbeat(fed.clusters[cl], node, list(cams), ts=wall() - 3, server=srv)
    view = ReadView(fed, lost_after=45, wall=wall)
    view.refresh()
    page = view.list(page=1, size=50)
    assert page["total"] == 200 and len(page["rows"]) == 50 and page["complete"]
    assert page["rows"][0]["as_of"] == "as of 3 s ago" and page["rows"][0]["node_state"] == "live"
    assert view.list(q="cam175")["rows"][0]["cluster"] == "south"
    assert view.list(cluster="south")["total"] == 50
    assert view.causes() == []


async def test_kill_a_server_one_cause_displayed():
    fed, links = await _four_nodes()
    wall = Clock(10_000.0)
    heartbeat(fed.clusters["north"], "node-1", list(range(1, 51)), ts=wall(), server="srv-1")
    heartbeat(fed.clusters["north"], "node-2", list(range(51, 101)), ts=wall(), server="srv-1")
    heartbeat(fed.clusters["north"], "node-3", list(range(101, 151)), ts=wall(), server="srv-2")
    heartbeat(fed.clusters["south"], "node-4", list(range(151, 201)), ts=wall(), server="srv-9")
    view = ReadView(fed, lost_after=45, wall=wall)
    view.refresh()
    wall.advance(100)                                                       # srv-1 died: node-1 and node-2 go silent together
    heartbeat(fed.clusters["north"], "node-3", list(range(101, 151)), ts=wall(), server="srv-2")
    heartbeat(fed.clusters["south"], "node-4", list(range(151, 201)), ts=wall(), server="srv-9")
    view.refresh()
    causes = view.causes()
    assert len(causes) == 1 and causes[0].scope == "server" and causes[0].name == "north/srv-1"
    assert causes[0].nodes == ["node-1", "node-2"] and causes[0].cameras == 100
    assert causes[0].sentence().startswith("server silent: north/srv-1 for 100 s")
    rows = view.list(size=200)["rows"]
    stale = [r for r in rows if r["node_state"] == "stale"]
    assert len(stale) == 100 and "last known state" in stale[0]["as_of"]   # still listed, greyed, with their age


async def test_unreachable_cluster_keeps_last_known_rows_and_says_so():
    fed, links = await _four_nodes()
    wall = Clock(10_000.0)
    heartbeat(fed.clusters["south"], "node-4", list(range(151, 201)), ts=wall(), server="srv-9")
    heartbeat(fed.clusters["north"], "node-1", list(range(1, 51)), ts=wall(), server="srv-1")
    view = ReadView(fed, lost_after=45, wall=wall)
    view.refresh()
    links["south"].up = False
    wall.advance(30)
    view.refresh()
    page = view.list(cluster="south", size=100)
    assert page["total"] == 50 and page["clusters"]["south"] == "unreachable" and not page["complete"]
    assert page["rows"][0]["node_state"] == "unreachable"
    assert view.causes()[0].scope == "cluster" and view.causes()[0].cameras == 50


class FakeNodeConsole:
    def __init__(self): self.edits = []
    def update_camera(self, camera, fields, subject):
        self.edits.append((camera, fields, subject)); return {"revision": len(self.edits)}
    def create_camera(self, fields, subject): return {"id": 999}


async def test_api_refuses_placement_and_is_idempotent():
    fed, _ = make_domain({"north": (), "south": ()}, "north")
    await publish_node(fed.clusters["south"], "node-4", [7])
    consoles = {"node-4": FakeNodeConsole()}
    api = ConsoleAPI(DomainDirectory(fed), consoles.__getitem__)
    r1 = api.update_camera(7, {"name": "gate"}, idempotency_key="k1")
    r2 = api.update_camera(7, {"name": "gate"}, idempotency_key="k1")            # the same PUT, not a second edit
    assert r1 is r2 and len(consoles["node-4"].edits) == 1 and r1["node"] == "node-4" and not r1["authenticated"]
    for bad in ({"node": "node-1"}, {"cluster": "north"}, {"placement": {}}, {"phase": "running"}):
        try:
            api.update_camera(7, bad, idempotency_key="k2"); raise AssertionError("must refuse")
        except ApiError as e:
            assert e.status == 400 and "may not set" in e.detail
    try:
        api.update_camera(99, {"name": "x"}, idempotency_key="k3"); raise AssertionError("must 404")
    except ApiError as e:
        assert e.status == 404


async def test_api_says_503_not_404_when_a_cluster_is_unreachable():
    fed, links = make_domain({"north": (), "south": ()}, "north")
    await publish_node(fed.clusters["south"], "node-4", [7])
    links["south"].up = False
    api = ConsoleAPI(DomainDirectory(fed), lambda n: FakeNodeConsole())
    try:
        api.update_camera(7, {"name": "x"}, idempotency_key="k"); raise AssertionError()
    except ApiError as e:
        assert e.status == 503 and "unreachable" in e.detail


def test_gateway_fans_out_and_the_node_sees_one_viewer():
    tees = {7: LiveTee(7)}
    def authorise(token, camera):
        if token != "alice-token": raise Forbidden(token)
        return "alice"
    ep = NodeLiveEndpoint("node-4", tees, authorise)
    gw = Gateway("gw-1", where=lambda c: "node-4", endpoint=lambda n: ep)
    viewers = [gw.watch(7, "alice-token", f"browser-{i}", maxsize=5) for i in range(50)]
    assert tees[7].viewers == 1 and gw.viewers(7) == 50                  # ONE subscription on the Node, fifty out
    try:
        gw.watch(7, "bad-token", "browser-x"); raise AssertionError("the Node decides")
    except Forbidden:
        pass
    for f in range(100):
        tees[7].push(f"frame-{f}")                                       # the recorder's push never blocks
    assert gw.pump() == 30 and tees[7].subscribers["gw-1"].dropped == 70  # upstream leaky queue (30) leaked
    assert all(len(v.q) == 5 and v.dropped == 25 for v in viewers)       # every slow viewer leaked its own
    for i in range(50):
        gw.leave(7, f"browser-{i}")
    assert tees[7].viewers == 0 and gw.viewers(7) == 0


def test_gateway_follows_a_failover():
    eps = {"node-4": NodeLiveEndpoint("node-4", {7: LiveTee(7)}, lambda t, c: "alice"),
           "node-5": NodeLiveEndpoint("node-5", {7: LiveTee(7)}, lambda t, c: "alice")}
    home = {"cam": "node-4"}
    gw = Gateway("gw", where=lambda c: home["cam"], endpoint=eps.__getitem__)
    gw.watch(7, "t", "b1")
    home["cam"] = "node-5"                                                # the Node moved; the directory says so
    assert gw.reconnect(7, "t") == "node-5" and eps["node-5"].tees[7].viewers == 1 and gw.viewers(7) == 1


async def test_console_over_http():
    fed, _ = make_domain({"north": (), "south": ()}, "north")
    await publish_node(fed.clusters["south"], "node-4", [7])
    heartbeat(fed.clusters["south"], "node-4", [7], ts=1000.0)
    view = ReadView(fed, wall=lambda: 1002.0)
    api = ConsoleAPI(DomainDirectory(fed), lambda n: FakeNodeConsole())
    con = Console(DomainDirectory(fed), view, api, refresh_interval=0.05)
    srv = con.serve(port=0)
    port = srv.server_address[1]
    try:
        import time; time.sleep(0.2)
        body = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/cameras"))
        assert body["total"] == 1 and body["rows"][0]["as_of"] == "as of 2 s ago"
        w = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/where/7"))
        assert w["node"] == "node-4" and w["complete"]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/cameras/7", data=b'{"name":"x"}', method="PUT",
                                     headers={"Idempotency-Key": "abc"})
        assert json.load(urllib.request.urlopen(req))["node"] == "node-4"
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/cameras/7", data=b'{"node":"node-1"}', method="PUT",
                                     headers={"Idempotency-Key": "def"})
        try:
            urllib.request.urlopen(req); raise AssertionError()
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        con.stop(srv)
