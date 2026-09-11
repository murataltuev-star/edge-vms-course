"""Lesson 5 — entitlement from the domain's side: cached, verified against
the product's vendor key, graceful for a stated period; recording never stops."""
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from domain.entitlement import GRACE, EntitlementCache, LicenceError
from tests.conftest import Clock, make_domain


def vendor_licence(key, domain, cameras, valid_until, issued):
    body = json.dumps({"domain": domain, "cameras": cameras, "features": ["record"], "valid_until": valid_until,
                       "issued": issued}, sort_keys=True).encode()
    return body + b"." + key.sign(body).hex().encode()


def test_licence_verified_cached_and_graceful():
    clk = Clock(1000.0)
    fed, links = make_domain({"north": ()}, "north")
    vendor = Ed25519PrivateKey.generate()
    pub = vendor.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ent = EntitlementCache("acme", fed.domain_cluster.vars, pub, now=clk)
    assert ent.status() == "none" and ent.may_add_camera(0) == (False, "entitlement none: recording continues, adding cameras does not")
    ent.install(vendor_licence(vendor, "acme", cameras=100, valid_until=clk() + 30 * 86400, issued=clk()))
    assert ent.status() == "valid" and ent.may_add_camera(99)[0] and not ent.may_add_camera(100)[0]
    try:
        ent.install(vendor_licence(Ed25519PrivateKey.generate(), "acme", 1000, clk() + 1e9, clk())); raise AssertionError()
    except LicenceError:
        pass                                                   # a licence the vendor did not sign
    try:
        ent.install(vendor_licence(vendor, "other-domain", 1000, clk() + 1e9, clk())); raise AssertionError()
    except LicenceError:
        pass                                                   # somebody else's licence
    clk.advance(31 * 86400)                                    # the licence server has been unreachable for a month
    assert ent.status() == "grace" and ent.may_add_camera(50)[0]
    clk.advance(GRACE)
    assert ent.status() == "degraded" and not ent.may_add_camera(50)[0]
    assert ent.recording_allowed()                             # by construction, in every state
