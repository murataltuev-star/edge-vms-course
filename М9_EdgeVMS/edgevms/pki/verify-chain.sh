#!/usr/bin/env bash
# Lesson 17, Step 3 — prove the signature check before trusting it.
# Same CMS primitive RAUC uses; no RAUC, no VM. Three outcomes, all expected:
#   1. signed by a key that chains to the keyring        -> accepted
#   2. signed by a valid key the keyring never heard of  -> refused (who signed this?)
#   3. good signature, changed content                   -> refused (is this what they signed?)
set -u
OUT="${OUT:-$(cd "$(dirname "$0")" && pwd)/out}"
cd "$OUT" || { echo "run pki/make-ca.sh first" >&2; exit 1; }
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT

printf 'this stands in for a rootfs image\n' > "$W/payload.bin"
printf 'this stands in for a TAMPERED rootfs image\n' > "$W/tampered.bin"
openssl cms -sign -in "$W/payload.bin" -signer dev.cert.pem -inkey dev.key.pem -binary -outform DER -out "$W/payload.sig"
openssl cms -sign -in "$W/payload.bin" -signer rogue.cert.pem -inkey rogue.key.pem -binary -outform DER -out "$W/rogue.sig"

# openssl exits 4 on a refusal, which is the point; judge by its message, not its status
verify() { openssl cms -verify -in "$1" -inform DER -CAfile keyring.pem -content "$2" -binary -out /dev/null 2>&1 || true; }
pass=0
if verify "$W/payload.sig" "$W/payload.bin" | grep -q successful;   then echo "1. good signer, good content ... accepted (OK)"; pass=$((pass+1)); else echo "1. UNEXPECTED: good bundle refused"; fi
if verify "$W/rogue.sig"   "$W/payload.bin" | grep -q "issuer";     then echo "2. rogue signer .............. refused: unable to get local issuer certificate (OK)"; pass=$((pass+1)); else echo "2. UNEXPECTED: rogue signer accepted"; fi
if verify "$W/payload.sig" "$W/tampered.bin" | grep -q "verif";     then echo "3. tampered content .......... refused: content verify error (OK)"; pass=$((pass+1)); else echo "3. UNEXPECTED: tampered content accepted"; fi
[ "$pass" -eq 3 ] && echo "all 3 as designed" || { echo "$pass/3"; exit 1; }
