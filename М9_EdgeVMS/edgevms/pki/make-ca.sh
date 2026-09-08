#!/usr/bin/env bash
# Lesson 17, Step 2 — a two-level CA on your own machine. Keys never exist
# on a device. The keyring that ships is the ROOT and nothing else: trust
# anchors only; the signing certificate travels inside the bundle.
#
# -nodes leaves keys unencrypted: fine for a lesson, wrong for production.
# A real root lives offline (М12 Lesson 36 has the ceremony).
set -euo pipefail
OUT="${OUT:-$(cd "$(dirname "$0")" && pwd)/out}"
ORG="${ORG:-Example VMS}"
mkdir -p "$OUT" && cd "$OUT"
[ -e ca.key.pem ] && { echo "$OUT already holds a CA; remove it to start over" >&2; exit 1; }

# root: ten years, because a root that expires bricks the update path fleet-wide
openssl req -x509 -newkey rsa:3072 -keyout ca.key.pem -out ca.cert.pem -nodes -days 3650 \
  -subj "/O=$ORG/CN=$ORG Update CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"

# issuing certificate: the one CI signs bundles with; rotatable without touching a device
openssl req -newkey rsa:3072 -keyout dev.key.pem -out dev.csr.pem -nodes \
  -subj "/O=$ORG/CN=$ORG Development-1"
openssl x509 -req -in dev.csr.pem -CA ca.cert.pem -CAkey ca.key.pem -CAcreateserial \
  -out dev.cert.pem -days 750 -sha256 \
  -extfile <(printf "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n")
openssl verify -CAfile ca.cert.pem dev.cert.pem

cp ca.cert.pem keyring.pem                      # the keyring is just the root
chmod 600 ./*.key.pem

# the attacker, for Step 3 and Lesson 18: a perfectly good CA the device has never heard of
openssl req -x509 -newkey rsa:3072 -keyout rogue.ca.key.pem -out rogue.ca.cert.pem \
  -nodes -days 3650 -subj "/O=Attacker/CN=Attacker CA" \
  -addext "basicConstraints=critical,CA:TRUE"
openssl req -newkey rsa:3072 -keyout rogue.key.pem -out rogue.csr.pem -nodes \
  -subj "/O=Attacker/CN=Attacker Signer"
openssl x509 -req -in rogue.csr.pem -CA rogue.ca.cert.pem -CAkey rogue.ca.key.pem \
  -CAcreateserial -out rogue.cert.pem -days 750 -sha256

echo
echo "keyring (ships in the image):  $OUT/keyring.pem"
echo "signer  (stays in CI):         $OUT/dev.cert.pem + dev.key.pem"
echo "attacker (Step 3, Lesson 18):  $OUT/rogue.cert.pem + rogue.key.pem"
