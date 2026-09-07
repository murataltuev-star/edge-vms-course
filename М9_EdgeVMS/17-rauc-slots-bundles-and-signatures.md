# Lesson 17 — RAUC: Slots, Bundles, and Signatures

**Module:** EdgeVMS — shipping the VMS as an appliance (Module 9)
**You will build:** a certificate authority, a signed update bundle, and a working `rauc install` that writes a new system into the inactive slot — plus three proofs that an unsigned, wrongly-signed, or tampered bundle is refused.
**Time:** ~90–120 minutes.

## Why this lesson exists

Lesson 16 left you with two slots and a human pressing a down arrow. This lesson replaces the human with a tool, and then spends most of its length on the part people skip: **why the box is allowed to trust the thing it is about to install.**

That ordering is deliberate. An appliance downloads updates over a network you do not control, from a URL an attacker may be able to influence, and installs them without asking anybody. The install mechanism is easy. The trust decision is the product.

> **What you can verify without hardware.** The entire signing chain — building a CA, signing, verifying, and watching bad signatures get rejected — runs on any machine with `openssl` and needs no VM at all. Every command in Steps 2 and 3 was run against **OpenSSL 3.0.13** while writing this lesson, and the outputs shown are real. Steps 4–6 need the Lesson 16 bench.

## Prerequisites

- **Lesson 16** — the QEMU bench with two slots, and the `/etc/slot-id` trick for telling them apart.
- `openssl` on your host: `openssl version` should report 3.x. (1.1.1 works for everything here too.)
- RAUC installed **inside the VM**. Debian bookworm has it: `apt install rauc`. Check with `rauc --version`.

## Learning objectives

1. Write a `system.conf` that describes your slot layout to RAUC, using the current keys rather than the deprecated ones.
2. Explain the difference between a *keyring* and a *certificate*, and say precisely what belongs in each.
3. Build a two-level CA and sign an update bundle with it.
4. Choose a bundle format deliberately, and explain what `verity` buys that `plain` does not.
5. Demonstrate — not assert — that a bundle signed by an untrusted key is rejected, and that a tampered bundle is rejected even when its signature is valid.
6. Install a bundle into the inactive slot and confirm the running slot was never touched.

---

## Step 1 — Describe the slots

RAUC reads `/etc/rauc/system.conf`. Boot **slot A** on your bench and create it:

```ini
[system]
compatible=example-vms-appliance-x86-64
bootloader=grub
grubenv=/boot/efi/grubenv
data-directory=/data/rauc
bundle-formats=-plain

[keyring]
path=/etc/rauc/keyring.pem

[slot.rootfs.0]
device=/dev/vda2
type=ext4
bootname=A

[slot.rootfs.1]
device=/dev/vda3
type=ext4
bootname=B
```

Every line there is a decision. Take them in turn.

**`compatible` is a hardware lock, and it is the cheapest safety feature you will ever ship.** RAUC refuses any bundle whose `compatible` string does not match this one exactly. It is what stops the Rev-B appliance image installing onto Rev-A hardware where the network interface is on a different bus. The documentation asks for a string "as specific as required to prevent faulty updating systems with the wrong firmware" — so put the hardware revision in it and change it when the hardware changes.

**`data-directory`, not `statusfile`.** RAUC needs somewhere to record per-slot state — which slot was installed when, and whether it has been marked good. Older material and many blog posts use `statusfile`; **that key is deprecated** and the documentation now says to use `data-directory` instead. It points at the data partition, because it must survive both slots being replaced.

**`bundle-formats=-plain`.** This is the sharp edge of the lesson and it is easy to miss. RAUC supports three bundle formats — `plain`, `verity` and `crypt` — and if you configure none of them it **still defaults to `plain`**, with only a warning. `plain` is the legacy format kept for compatibility with RAUC 1.4 and earlier. The `-plain` syntax means "everything except plain", so a `plain` bundle is refused outright rather than warned about. Set this on day one; the day you need HTTP streaming you will need `verity` anyway.

**Slot sections are `[slot.<class>.<index>]`.** The class (`rootfs`) groups slots that are alternatives for the same job; the index distinguishes them. `bootname` is what connects a slot to the bootloader — it must match the names GRUB knows, which in Lesson 16 were `A` and `B`, and it must be unique across all slots.

Now check RAUC agrees with you:

```bash
mkdir -p /data/rauc
rauc status
```

You should see your compatible string and both slots, with the booted one marked. If RAUC complains about the keyring, good — you have not made one yet. That is Step 2.

## Step 2 — A certificate authority, on your own machine

Everything in this step runs on your **host**, not the VM. Keys for signing updates should never exist on a device.

The mental model first, because the vocabulary trips people:

- A **key** signs. It is secret. If it leaks, an attacker can ship updates to every appliance you have ever sold.
- A **certificate** says "this public key belongs to this identity", and is itself signed by somebody else.
- A **keyring** is the set of certificates the device trusts *without further proof* — its trust anchors. This is the thing baked into the image.

The critical rule, and RAUC's documentation is emphatic about it:

> **The keyring holds trust anchors only.** Intermediate certificates needed to build a chain to those anchors should **not** be added to the keyring — they travel inside the bundle.

Put a root CA in the keyring and it can vouch for signing certificates you have not issued yet. Put a signing certificate in the keyring and you can never rotate it without shipping a new OS image to every device in the field. Only one of those is a product you can operate.

Build the root:

```bash
mkdir -p ~/edge-pki && cd ~/edge-pki

openssl req -x509 -newkey rsa:3072 -keyout ca.key.pem -out ca.cert.pem -nodes -days 3650 \
  -subj "/O=Example VMS/CN=Example VMS Update CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign"
```

Ten years, because a root that expires bricks your update path across the whole fleet. `-nodes` leaves the key unencrypted, which is fine for a lesson and **wrong for production** — a real root key lives offline, on a smartcard or in an HSM, and is used a handful of times a year. М13 Lesson 39 comes back to this with a signing ceremony.

Now an issuing certificate — the one you actually sign bundles with:

```bash
openssl req -newkey rsa:3072 -keyout dev.key.pem -out dev.csr.pem -nodes \
  -subj "/O=Example VMS/CN=Example VMS Development-1"

openssl x509 -req -in dev.csr.pem -CA ca.cert.pem -CAkey ca.key.pem -CAcreateserial \
  -out dev.cert.pem -days 750 -sha256 \
  -extfile <(printf "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n")
```

Two levels, not one, and the reason is operational: the signing key is used constantly by CI and will eventually be compromised or simply need rotating. When that happens you issue a new one from the same root and **every device in the field accepts it immediately**, because what they trust is the root. With a single self-signed key, that same event is a fleet-wide OS update — assuming the fleet still trusts you enough to accept one.

Confirm the chain:

```bash
openssl verify -CAfile ca.cert.pem dev.cert.pem
```

```
dev.cert.pem: OK
```

The keyring you will ship is just the root:

```bash
cp ca.cert.pem keyring.pem
```

## Step 3 — Prove the signature check works, before trusting it

Do this **now**, before building a bundle, because it takes two minutes and it is the whole basis of the design. RAUC signs with CMS (Cryptographic Message Syntax); `openssl cms` is the same primitive, so you can watch it behave with no RAUC and no VM involved.

Make something to sign and sign it:

```bash
cd ~/edge-pki
printf 'this stands in for a rootfs image\n' > payload.bin

openssl cms -sign -in payload.bin -signer dev.cert.pem -inkey dev.key.pem \
  -binary -outform DER -out payload.sig
```

Verify against the keyring:

```bash
openssl cms -verify -in payload.sig -inform DER -CAfile keyring.pem \
  -content payload.bin -binary -out /dev/null
```

```
CMS Verification successful
```

Note what that proved: the signature was made by a key whose certificate **chains to the root in the keyring**. The device never saw `dev.cert.pem` in advance. That is delegation working.

Now be the attacker. Build a completely separate CA and signing certificate:

```bash
openssl req -x509 -newkey rsa:3072 -keyout rogue.ca.key.pem -out rogue.ca.cert.pem \
  -nodes -days 3650 -subj "/O=Attacker/CN=Attacker CA" \
  -addext "basicConstraints=critical,CA:TRUE"

openssl req -newkey rsa:3072 -keyout rogue.key.pem -out rogue.csr.pem -nodes \
  -subj "/O=Attacker/CN=Attacker Signer"

openssl x509 -req -in rogue.csr.pem -CA rogue.ca.cert.pem -CAkey rogue.ca.key.pem \
  -CAcreateserial -out rogue.cert.pem -days 750 -sha256
```

Sign the same payload with it, and verify against **your** keyring:

```bash
openssl cms -sign -in payload.bin -signer rogue.cert.pem -inkey rogue.key.pem \
  -binary -outform DER -out rogue.sig

openssl cms -verify -in rogue.sig -inform DER -CAfile keyring.pem \
  -content payload.bin -binary -out /dev/null
```

```
CMS Verification failure
...:CMS routines:cms_signerinfo_verify_cert:certificate verify error:...:
    Verify error: unable to get local issuer certificate
```

A perfectly valid signature, cryptographically sound, and correctly refused — because it chains to a root your device has never heard of. This is the entire security model in one command.

One more, and it is the case people forget. Keep the **good** signature and change the payload:

```bash
printf 'this stands in for a TAMPERED rootfs image\n' > tampered.bin

openssl cms -verify -in payload.sig -inform DER -CAfile keyring.pem \
  -content tampered.bin -binary -out /dev/null
```

```
CMS Verification failure
...:CMS routines:CMS_SignerInfo_verify_content:verification failure:...
...:CMS routines:CMS_verify:content verify error:...
```

Different error, same refusal. The first check asked *who signed this*; the second asked *is this what they signed*. **A design that only answers the first question ships a signed header attached to arbitrary content**, which is a real class of bug and not a hypothetical one.

## Step 4 — Build the bundle

A RAUC bundle is a directory containing your images and a manifest, packed and signed. Build the directory:

```bash
mkdir -p ~/edge-bundle/content && cd ~/edge-bundle
```

You need a root filesystem image. Take the one you already have — extract slot A from the bench disk into a file:

```bash
sudo qemu-nbd --connect=/dev/nbd0 ~/edge-bench/appliance.qcow2
sudo dd if=/dev/nbd0p2 of=content/rootfs.ext4 bs=4M status=progress
sudo qemu-nbd --disconnect /dev/nbd0
sudo chown $USER content/rootfs.ext4
```

That is a full 8 GB file. Shrink it if disk space is tight (`e2fsck -f` then `resize2fs -M`), but it works as-is.

The manifest, `content/manifest.raucm`:

```ini
[update]
compatible=example-vms-appliance-x86-64
version=2026.09-1

[bundle]
format=verity

[image.rootfs]
filename=rootfs.ext4
```

Small file, four things worth saying about it:

- **`compatible` must match `system.conf` exactly.** This is the hardware lock from Step 1, seen from the other side.
- **`version` is free-form and RAUC does not validate it.** It will not stop you installing an older version over a newer one. If monotonic versions matter to your product — and they do — that is `min-bundle-version` in `system.conf`, or your own logic. Do not assume the tool is protecting you here.
- **`format=verity`.** Not the default. `verity` builds a dm-verity hash tree over the bundle so the payload is verified *block by block as it is read*, rather than once up front — and it is what makes installing directly from an HTTP URL possible.
- **No `sha256`, no `size`.** RAUC computes those at bundle time and writes them into the manifest inside the bundle. You do not maintain them, and material telling you to is out of date.

Pack and sign it:

```bash
rauc bundle --cert=~/edge-pki/dev.cert.pem --key=~/edge-pki/dev.key.pem \
  content/ update-2026.09-1.raucb
```

Inspect what you made:

```bash
rauc info --keyring=~/edge-pki/keyring.pem update-2026.09-1.raucb
```

You should see the compatible string, the version, the format, and the signer's certificate chain. Note that `rauc info` **requires a keyring** to authenticate what it is showing you — there is a `--no-verify` escape hatch for inspecting an untrusted bundle, and its existence is the reminder that everything printed without it is unverified claims from a stranger.

`rauc install` has no such escape hatch. Signing is mandatory; verification is not optional.

## Step 5 — Install into the other slot

Copy the bundle into the VM (`scp`, a shared folder, or a second disk image — whatever your bench allows), install the keyring, and install:

```bash
# inside the VM, booted from slot A
cp keyring.pem /etc/rauc/keyring.pem
rauc install update-2026.09-1.raucb
```

Watch the output. RAUC verifies the signature, checks `compatible`, selects the **inactive** slot as the target, writes the image, and marks the slot for the next boot. It never asks which slot to use — that is not a decision an operator should be making, and Lesson 16's design is what makes it derivable.

Confirm:

```bash
rauc status
```

The booted slot is still `rootfs.0`; `rootfs.1` now carries the new install and is activated for next boot.

Then the check that matters:

```bash
cat /etc/slot-id      # still "slot A"
findmnt /             # still read-only, still /dev/vda2
```

**Nothing about the running system changed.** An 8 GB write completed and the system you are logged into is byte-identical to what it was. That is atomicity — not a promise in a document, an observation at a prompt.

## Step 6 — Break it three ways

Take the same three proofs from Step 3 and run them against the real tool.

**Wrong signer.** Build a bundle signed by the rogue key:

```bash
rauc bundle --cert=~/edge-pki/rogue.cert.pem --key=~/edge-pki/rogue.key.pem \
  content/ rogue.raucb
```

That succeeds — anybody can *make* a bundle. Copy it in and install it:

```bash
rauc install rogue.raucb
```

It fails, and it fails **before writing anything**. Confirm with `rauc status` that the inactive slot still holds what Step 5 put there.

**Wrong hardware.** Edit the manifest's `compatible` to `example-vms-appliance-armhf`, rebuild, sign properly, install. Refused. The signature was perfect; the box is simply not what the bundle is for.

**Tampered payload.** Flip a byte in a correctly signed bundle:

```bash
cp update-2026.09-1.raucb tampered.raucb
printf '\x00' | dd of=tampered.raucb bs=1 seek=5000000 conv=notrunc
rauc install tampered.raucb
```

Refused. With `verity`, corruption anywhere in the payload is caught by the hash tree as the block is read.

**Write down what each of the three refusals actually protected against.** They are three different attacks — an impostor, a mismatch, and a man in the middle — and a student who can name which is which understands the update path better than most people shipping one.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `rauc status` says "no keyring" | `[keyring] path=` points somewhere that does not exist. It is relative to `system.conf` unless absolute. |
| Bundle install fails with a compatible mismatch you did not expect | Whitespace or a typo — the comparison is exact. Compare `rauc info` output against `rauc status` output character by character. |
| RAUC warns about defaulting to the `plain` format | You omitted `bundle-formats` in `system.conf` **or** `format=` in the manifest. Set both; see Step 1. |
| `rauc bundle` fails with a key error | Check the paths — `~` is not expanded in every context. Use absolute paths if in doubt. |
| Install fails: "target slot is booted" | RAUC will not overwrite the running slot. If both slots report as booted, `bootname` values in `system.conf` do not match what the bootloader set — check the `rauc.slot=` kernel argument from Lesson 16. |
| Install from an HTTP URL fails though the bundle is `verity` | Streaming also needs a server supporting HTTP Range requests and NBD support in the kernel. Test with a local file first to isolate. |
| `openssl cms -verify` fails on a bundle you believe is good | You are probably verifying against `dev.cert.pem` rather than `keyring.pem`. The keyring is the root, not the signer. |

## Recap

- `system.conf` describes slots to RAUC. Use `data-directory`, not the deprecated `statusfile`, and set `bundle-formats=-plain` explicitly — the default is still the legacy format.
- `compatible` is a hardware lock and the cheapest safety feature in the module. Put the hardware revision in it.
- **The keyring holds trust anchors only.** A two-level CA lets you rotate the signing key without touching a single deployed device; a single self-signed key makes key rotation a fleet-wide OS update.
- Signing is mandatory in RAUC. `rauc info` has `--no-verify`; `rauc install` does not.
- Three distinct refusals, each demonstrated rather than asserted: **wrong signer** (chains to an unknown root), **wrong hardware** (compatible mismatch), **tampered payload** (valid signature, changed content). The third is the one naive designs miss.
- An 8 GB install completed while the running system stayed byte-identical. That is what atomic means, observed rather than promised.

## Exercises

1. Add an **intermediate** CA between your root and your signing certificate, and use `rauc bundle --intermediate=` to carry it in the bundle. Confirm the device still accepts it with **only the root** in its keyring — and explain why putting the intermediate in the keyring instead would be a mistake.
2. Set `min-bundle-version` in `system.conf` and demonstrate a downgrade being refused. Then explain why `version` alone did not do this for you.
3. Serve a bundle over HTTP (`python3 -m http.server`) and install from the URL. If it fails, work out which of the three streaming prerequisites your bench is missing.
4. Compare the disk footprint and `rauc info` output of the same content bundled as `plain` and as `verity`. Name one thing `verity` makes possible that `plain` does not.
5. Delete `/etc/rauc/keyring.pem` from the running system and try to install a known-good bundle. Then argue both sides: should a device with no keyring refuse everything, or fall back to something? (There is a right answer here. Say why.)

## Where this is going

You can now ship a signed system into the inactive slot and reboot into it. What you cannot yet do is survive that new system being *broken* — it boots, or it does not, and if it does not, nobody is there to press the down arrow.

Lesson 18 closes that. GRUB gets boot-attempt logic, the new slot has to *prove* it works before it is trusted, and you will ship a deliberately broken update to watch the appliance rescue itself with nobody in the room.
