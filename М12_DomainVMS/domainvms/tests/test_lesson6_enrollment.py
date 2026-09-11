"""Lesson 6 — a box enrolls from cold with nobody typing a secret."""
from domain.enroll import EnrollError, Manufacturer, Pledge, Registrar, finish
from domain.signer import Signer, TrustBundle
from tests.conftest import Clock, make_domain


def _setup(clk, with_masa=True):
    fed, _ = make_domain({"north": ()}, "north")
    signer = Signer("acme", fed.domain_cluster.vars, now=clk)
    bundle = TrustBundle([signer.root.cert])
    vendor = Manufacturer("vendor", now=clk)
    reg = Registrar("acme", signer, vendor.ca_cert, vendor.masa_public if with_masa else None, approval_ttl=3600, now=clk)
    return fed, signer, bundle, vendor, reg


def test_zero_touch_with_a_voucher():
    clk = Clock(1000.0)
    fed, signer, bundle, vendor, reg = _setup(clk)
    cert, key = vendor.provision("SN-0001")                    # at the factory: an IDevID
    vendor.sell("SN-0001", "acme")
    box = Pledge("SN-0001", cert, key, temp_credential="hand-typed-secret")
    hello = box.hello(nonce="n1")
    voucher = vendor.voucher("SN-0001", reg.id, "n1")           # the one thing the customer needs from the vendor
    ldevid = reg.enroll_with_voucher(hello, voucher)
    finish(box, ldevid, bundle, now=clk())
    assert box.temp_credential is None and box.ldevid is ldevid            # the stand-in is deleted; nothing stops
    assert bundle.verify(ldevid, now=clk()) == "acme root g1"
    assert ldevid.subject.rfc4514_string().startswith("CN=SN-0001")        # names the box, not a server
    assert reg.audit[-1]["how"] == "voucher" and reg.audit[-1]["serial"] == "SN-0001"
    try:
        reg.enroll_with_voucher(box.hello("n2"), voucher); raise AssertionError()
    except EnrollError:
        pass                                                   # a voucher is for one nonce


def test_a_stranger_and_a_wrong_voucher_are_refused():
    clk = Clock(1000.0)
    fed, signer, bundle, vendor, reg = _setup(clk)
    other = Manufacturer("someone-else", now=clk)
    cert, key = other.provision("SN-FAKE")
    try:
        reg.request(Pledge("SN-FAKE", cert, key).hello("n")); raise AssertionError()
    except Exception as e:
        assert "no trusted root" in str(e)                     # not our manufacturer's IDevID
    cert, key = vendor.provision("SN-0002")
    vendor.sell("SN-0002", "someone-elses-domain")
    try:
        vendor.voucher("SN-0002", reg.id, "n"); raise AssertionError()
    except EnrollError as e:
        assert "not sold to" in str(e)                         # the MASA vouches only for its own sales


def test_registration_with_approval_queue_audit_and_expiry():
    clk = Clock(1000.0)
    fed, signer, bundle, vendor, reg = _setup(clk, with_masa=False)
    cert, key = vendor.provision("SN-0003")
    box = Pledge("SN-0003", cert, key)
    try:
        reg.enroll_with_voucher(box.hello("n"), b"x.y"); raise AssertionError()
    except EnrollError as e:
        assert "no MASA key" in str(e)
    reg.request(box.hello("n1"))
    assert list(reg.pending) == ["SN-0003"]
    try:
        reg.approve("SN-9999", by="carol"); raise AssertionError()
    except EnrollError:
        pass
    ldevid = reg.approve("SN-0003", by="carol")
    finish(box, ldevid, bundle, now=clk())
    assert [a["how"] for a in reg.audit] == ["requested", "approved", "approved by carol"]
    assert reg.audit[1]["by"] == "carol"
    cert, key = vendor.provision("SN-0004")
    reg.request(Pledge("SN-0004", cert, key).hello("n"))
    clk.advance(3601)
    assert reg.expire_pending() == ["SN-0004"]                 # unapproved requests do not wait forever
    assert reg.audit[-1]["how"] == "expired unapproved"
