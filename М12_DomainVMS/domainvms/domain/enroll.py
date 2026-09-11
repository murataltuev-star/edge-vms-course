"""Lesson 6 — secure introduction: a box joins the domain.

A device with no secret must obtain one, over a network it does not yet
trust, from a service it cannot yet authenticate — and М9 removed the
easy answer: the image is byte-identical across units, so nothing
device-specific can be inside it.

The ladder, in BRSKI's vocabulary (RFC 8995):

    pledge      the box; carries a factory IDevID (manufacturer-signed certificate)
    registrar   the DOMAIN's door; decides yes/no; hands the pledge to the signer
    MASA        the manufacturer's authority; issues a VOUCHER naming which registrar to trust
    LDevID      the certificate from THIS domain, replacing Lesson 4's hand-provisioned credential

Two paths, both auditable: the voucher path (zero-touch — the only thing
the customer ever needs from the vendor is the voucher) and registration
with human approval (the shipped fallback: a queue, an audit trail, an
expiry on unapproved requests). Never a shared secret in an image.

The manufacturer here is simulated: a CA for IDevIDs and a MASA key for
vouchers. A TPM cannot be faked in any way worth teaching; the attestation
section of the lesson is read, not run.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization

from .signer import Signer, TrustBundle, VerifyError, _name, _utc, DAY, YEAR


class EnrollError(Exception):
    pass


# -- the vendor's side (М14): a manufacturer CA and a MASA -----------------------

class Manufacturer:
    def __init__(self, name: str = "vendor", now=time.time):
        self.name, self.now = name, now
        self.ca_key = Ed25519PrivateKey.generate()
        self.ca_cert = (x509.CertificateBuilder().subject_name(_name(f"{name} IDevID CA", name))
                        .issuer_name(_name(f"{name} IDevID CA", name)).public_key(self.ca_key.public_key())
                        .serial_number(x509.random_serial_number()).not_valid_before(_utc(now() - 60))
                        .not_valid_after(_utc(now() + 20 * YEAR))
                        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(self.ca_key, None))
        self.masa_key = Ed25519PrivateKey.generate()
        self.sold: dict[str, str] = {}          # serial -> customer domain (what the MASA knows)

    @property
    def masa_public(self) -> bytes:
        return self.masa_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def provision(self, serial: str) -> tuple[x509.Certificate, Ed25519PrivateKey]:
        """At the factory: an IDevID, long-lived, naming the serial. The key
        would live in a TPM on real hardware."""
        key = Ed25519PrivateKey.generate()
        cert = (x509.CertificateBuilder().subject_name(_name(serial, self.name)).issuer_name(self.ca_cert.subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(_utc(self.now() - 60)).not_valid_after(_utc(self.now() + 20 * YEAR))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True).sign(self.ca_key, None))
        return cert, key

    def sell(self, serial: str, domain: str) -> None:
        self.sold[serial] = domain

    def voucher(self, serial: str, registrar_id: str, nonce: str) -> bytes:
        """The MASA vouches: this serial may trust THIS registrar. The single
        cryptographic thing a customer ever needs from the vendor."""
        if self.sold.get(serial) != registrar_id.split("/")[0]:
            raise EnrollError(f"MASA: {serial} was not sold to the domain behind {registrar_id}")
        body = json.dumps({"serial": serial, "registrar": registrar_id, "nonce": nonce,
                           "exp": self.now() + DAY}, sort_keys=True).encode()
        return body + b"." + self.masa_key.sign(body).hex().encode()   # hex, so the separator never appears inside the signature


# -- the pledge ------------------------------------------------------------------

@dataclass
class Pledge:
    serial: str
    idevid: x509.Certificate
    idevid_key: Ed25519PrivateKey
    temp_credential: str | None = None       # Lesson 4's hand-provisioned stand-in, to be deleted
    ldevid: x509.Certificate | None = None
    ldevid_key: Ed25519PrivateKey | None = None

    def hello(self, nonce: str) -> dict:
        key = Ed25519PrivateKey.generate()
        self.ldevid_key = key
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        signed = json.dumps({"serial": self.serial, "nonce": nonce, "csr_pub": pub.hex()}, sort_keys=True).encode()
        return {"serial": self.serial, "nonce": nonce, "csr_pub": pub.hex(),
                "idevid": self.idevid.public_bytes(serialization.Encoding.PEM).decode(),
                "proof": self.idevid_key.sign(signed).hex()}


# -- the registrar: the domain's door ----------------------------------------------

@dataclass
class Pending:
    serial: str
    hello: dict
    requested_at: float
    expires_at: float


class Registrar:
    def __init__(self, domain: str, signer: Signer, manufacturer_root: x509.Certificate, masa_public: bytes | None,
                 approval_ttl: float = 24 * 3600.0, now=time.time):
        self.id = f"{domain}/registrar"
        self.signer, self.now = signer, now
        self.vendor = TrustBundle([manufacturer_root])
        self.masa_public = masa_public
        self.approval_ttl = approval_ttl
        self.pending: dict[str, Pending] = {}
        self.audit: list[dict] = []

    def _check_hello(self, hello: dict) -> x509.Certificate:
        cert = x509.load_pem_x509_certificate(hello["idevid"].encode())
        self.vendor.verify(cert, now=self.now())            # a real IDevID from a manufacturer we trust
        signed = json.dumps({"serial": hello["serial"], "nonce": hello["nonce"], "csr_pub": hello["csr_pub"]},
                            sort_keys=True).encode()
        try:
            cert.public_key().verify(bytes.fromhex(hello["proof"]), signed)
        except InvalidSignature:
            raise EnrollError("the pledge does not hold the IDevID's key")
        from cryptography.x509.oid import NameOID
        if cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value != hello["serial"]:
            raise EnrollError("IDevID names a different serial")
        return cert

    def _check_voucher(self, voucher: bytes, serial: str, nonce: str) -> None:
        if self.masa_public is None:
            raise EnrollError("this domain has no MASA key; use registration with approval")
        body, sig = voucher.rsplit(b".", 1)
        try:
            Ed25519PublicKey.from_public_bytes(self.masa_public).verify(bytes.fromhex(sig.decode()), body)
        except (InvalidSignature, ValueError):
            raise EnrollError("voucher signature does not verify")
        v = json.loads(body)
        if v["serial"] != serial or v["registrar"] != self.id or v["nonce"] != nonce or v["exp"] < self.now():
            raise EnrollError("voucher does not name this pledge, this registrar and this nonce, or has expired")

    def _issue(self, hello: dict, how: str) -> x509.Certificate:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(hello["csr_pub"]))
        cert = self.signer.issue(hello["serial"], "ldevid", pub)        # EST, in one line
        self.audit.append({"at": self.now(), "serial": hello["serial"], "how": how, "ldevid_serial": cert.serial_number})
        return cert

    # -- path 1: zero-touch with a voucher --------------------------------------------
    def enroll_with_voucher(self, hello: dict, voucher: bytes) -> x509.Certificate:
        self._check_hello(hello)
        self._check_voucher(voucher, hello["serial"], hello["nonce"])
        return self._issue(hello, "voucher")

    # -- path 2: registration with human approval --------------------------------------
    def request(self, hello: dict) -> str:
        self._check_hello(hello)
        self.expire_pending()
        self.pending[hello["serial"]] = Pending(hello["serial"], hello, self.now(), self.now() + self.approval_ttl)
        self.audit.append({"at": self.now(), "serial": hello["serial"], "how": "requested"})
        return hello["serial"]

    def approve(self, serial: str, by: str) -> x509.Certificate:
        self.expire_pending()
        p = self.pending.pop(serial, None)
        if p is None:
            raise EnrollError(f"{serial}: no pending request (expired, or never asked)")
        self.audit.append({"at": self.now(), "serial": serial, "how": "approved", "by": by})
        return self._issue(p.hello, f"approved by {by}")

    def reject(self, serial: str, by: str) -> None:
        if self.pending.pop(serial, None):
            self.audit.append({"at": self.now(), "serial": serial, "how": "rejected", "by": by})

    def expire_pending(self) -> list[str]:
        gone = [s for s, p in self.pending.items() if p.expires_at <= self.now()]
        for s in gone:
            del self.pending[s]
            self.audit.append({"at": self.now(), "serial": s, "how": "expired unapproved"})
        return gone


def finish(pledge: Pledge, ldevid: x509.Certificate, bundle: TrustBundle, now: float) -> None:
    """The box keeps its LDevID, deletes the hand-provisioned credential —
    and nothing stops, because the LDevID verifies under the domain's root."""
    bundle.verify(ldevid, now=now)
    pledge.ldevid = ldevid
    pledge.temp_credential = None
