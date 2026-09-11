"""Lesson 7 — lifetimes, rotation, and revocation that works offline: the
outage arithmetic, renewal with overlap, root rotation under load, clock
skew as a named failure."""
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from domain.signer import DAY, LIFETIMES, Signer, TrustBundle, VerifyError, max_tolerable_outage
from tests.conftest import Clock, make_domain


def _signer(clk):
    fed, _ = make_domain({"north": ()}, "north")
    s = Signer("acme", fed.domain_cluster.vars, now=clk)
    return s, TrustBundle([s.root.cert])


def test_the_number_to_state():
    assert max_tolerable_outage("service") == 2 * DAY
    assert max_tolerable_outage("ldevid") == LIFETIMES["ldevid"]["lifetime"] - 90 * DAY
    # A product promising thirty days of autonomy cannot issue service certificates like these:
    assert max_tolerable_outage("service") < 30 * DAY
    assert max_tolerable_outage("ldevid") > 30 * DAY               # the device identity survives the outage


def test_thirty_day_outage_service_certs_expire_devices_do_not():
    clk = Clock(1_000_000.0)
    s, bundle = _signer(clk)
    pub = Ed25519PrivateKey.generate().public_key()
    svc, dev = s.issue("read-view", "service", pub), s.issue("SN-0001", "ldevid", pub)
    clk.advance(30 * DAY)                                          # the cluster could not reach the signer for a month
    try:
        bundle.verify(svc, now=clk()); raise AssertionError()
    except VerifyError as e:
        assert "expired" in str(e)                                 # service-to-service: stated as 2 days; it is dark
    assert bundle.verify(dev, now=clk())                           # the box is still the box


def test_renewal_overlaps_so_nothing_drops():
    clk = Clock(1_000_000.0)
    s, bundle = _signer(clk)
    pub = Ed25519PrivateKey.generate().public_key()
    c1 = s.issue("read-view", "service", pub)
    assert not s.needs_renewal(c1, "service")
    clk.advance(2 * DAY + 1)                                       # inside the margin
    assert s.needs_renewal(c1, "service")
    c2 = s.renew(c1, "service")
    assert bundle.verify(c1, now=clk()) and bundle.verify(c2, now=clk())     # both valid: reload without dropping
    assert c2.public_key().public_bytes_raw() == c1.public_key().public_bytes_raw() and c2.subject == c1.subject


def test_root_rotation_is_a_drill_the_domain_runs_through():
    clk = Clock(1_000_000.0)
    s, bundle = _signer(clk)
    pub = Ed25519PrivateKey.generate().public_key()
    old_leaf = s.issue("SN-0003", "ldevid", pub)                  # long-lived: it outlives the overlap
    new_root, cross = s.rotate_root(bundle, overlap=7 * DAY)
    new_leaf = s.issue("SN-0004", "ldevid", pub)
    assert bundle.verify(old_leaf, now=clk()) == "acme root g1" and bundle.verify(new_leaf, now=clk()) == "acme root g2"
    old_only = TrustBundle([[r for r, _ in bundle.roots.values()][0]])
    assert old_only.verify(new_leaf, now=clk(), cross=[cross]) == "acme root g2"   # a peer with only the old root, via the cross-cert
    clk.advance(7 * DAY + 1)
    try:
        bundle.verify(old_leaf, now=clk()); raise AssertionError()
    except VerifyError as e:
        assert "retired" in str(e)                                 # the old root is retired on the date stated
    assert bundle.verify(new_leaf, now=clk())
    s2 = Signer("acme", s.vars, now=clk)                           # the signer restarted elsewhere: same root g2
    assert s2.root.cert.subject == new_root.cert.subject


def test_revocation_is_a_lifetime_problem_and_clock_skew_is_named():
    clk = Clock(1_000_000.0)
    s, bundle = _signer(clk)
    pub = Ed25519PrivateKey.generate().public_key()
    dev = s.issue("SN-0002", "ldevid", pub, lifetime=3 * DAY)     # revoke = do not renew; stated: gone in 3 days
    assert bundle.verify(dev, now=clk())
    clk.advance(3 * DAY + 400)
    try:
        bundle.verify(dev, now=clk()); raise AssertionError()
    except VerifyError:
        pass
    fresh = s.issue("SN-0003", "ldevid", pub)
    try:
        bundle.verify(fresh, now=clk() - 3600); raise AssertionError()      # a peer whose clock is an hour behind
    except VerifyError as e:
        assert "clock skew" in str(e)
    assert bundle.verify(fresh, now=clk() - 200)                   # inside the 5-minute tolerance
