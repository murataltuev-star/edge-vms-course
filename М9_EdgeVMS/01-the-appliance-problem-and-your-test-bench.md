# Lesson 1 — The Appliance Problem, and Your Test Bench

**Module:** EdgeVMS — shipping the VMS as an appliance (Module 9)
**You will build:** a QEMU x86-64 UEFI virtual machine with a full A/B partition layout, booting either root filesystem on demand.
**Time:** ~90 minutes.

## Why this lesson exists

Everything you have built so far assumed a computer somebody administers. You installed packages, you edited files, you restarted services, and if something broke you fixed it at a prompt.

An appliance is a computer nobody administers. It sits in a ceiling void in a warehouse, or in a locked cabinet behind a till, and the only person who will ever touch it physically is an electrician who does not know what it does. It has to update itself over a network, and if an update goes wrong it has to recover **by itself**, because the alternative is a van.

That single constraint invalidates the update model you have been using your whole career. This lesson is about what replaces it, and about building the bench you will spend the next three lessons breaking.

> **What you can verify without hardware.** Everything in this lesson is a real VM you run on your own machine — no appliance hardware needed. If your host cannot do virtualisation (a locked-down work laptop, a cloud shell), read the *Without KVM* note in Step 1 before you start; the lessons still work, just slower.

## Prerequisites

- **М8 Lesson 3** — containers and images, and why credentials are passed by name rather than baked in. The same instinct drives this whole module.
- **М8 Lesson 6** — `config.py` reads settings but never credentials. That discipline is what makes an appliance image shippable at all, and Lesson 4 gives it teeth.
- A Linux host, or a Linux VM with nested virtualisation. macOS and Windows work through a Linux VM; running QEMU natively on either is possible but not a road this course maps.
- Installed: `qemu-system-x86_64`, `qemu-utils`, `ovmf` (the UEFI firmware), `parted` or `sfdisk`, and `e2fsprogs`.
  - **Debian/Ubuntu:** `sudo apt install qemu-system-x86 qemu-utils ovmf parted e2fsprogs`
  - Verify with `qemu-system-x86_64 --version` and `ls /usr/share/OVMF/OVMF_CODE.fd`.

## Learning objectives

1. Explain why in-place package updates are unsafe on an unattended device, in terms of the specific failure they cannot survive.
2. Describe the A/B (dual-slot) model: what is duplicated, what is not, and why.
3. Justify each partition in an appliance layout, and size the data partition from a stated outage requirement rather than a guess.
4. Build and boot a QEMU UEFI virtual machine with two independent root filesystems.
5. Select which slot boots, by hand, and prove the other one is untouched.

---

## Step 1 — The failure that decides everything

Here is the update you have always run:

```bash
sudo apt update && sudo apt upgrade
```

Ask what happens if the power fails **during** it. Not before, not after — during, while `dpkg` has unpacked half of a new `libc` and not yet finished configuring it.

On your laptop: you boot a rescue USB, run `dpkg --configure -a`, and swear. Twenty minutes.

On an appliance in a ceiling void: the box is now a brick that does not come back on the network. There is no rescue USB and nobody to hold it. The only recovery is a physical visit, and if you shipped that update to four hundred boxes, it is four hundred visits.

The problem is not that `apt` is bad. The problem is structural:

> **An in-place update passes through states that are neither the old system nor the new one.** If power is lost in one of those states, there is nothing to go back to, because the old system was consumed to make the new one.

Every property an appliance update needs follows from refusing that:

| Property | What it means |
|---|---|
| **Atomic** | At every instant the box holds a complete, bootable system. There is no half-updated state to be interrupted in |
| **Reversible** | If the new system boots badly, the old one is still there, whole |
| **Verified** | The box refuses to install anything not signed by you, because it will install unattended over a network you do not own |
| **Unattended** | Recovery requires no human. The box detects its own failure and undoes it |

### Without KVM

Check whether you have hardware acceleration:

```bash
ls -l /dev/kvm
```

If that file exists and you can read it, add `-enable-kvm` to every `qemu-system-x86_64` command in this module. If it does not, everything still works — QEMU falls back to full emulation, boots take perhaps thirty seconds instead of three, and nothing in these lessons is timing-sensitive. Do not spend an afternoon fighting for KVM on a machine that will not give it to you.

## Step 2 — A/B, and what is *not* duplicated

The answer the embedded industry settled on is to keep two complete root filesystems and boot one of them.

```
        ┌────────────────────────────────────────┐
        │  ESP  │  rootfs A  │  rootfs B  │ data │
        └────────────────────────────────────────┘
           ▲         ▲            ▲         ▲
           │         │            │         └── never duplicated, never wiped
           │         │            └── inactive: the update is written HERE
           │         └── active: currently running, read-only
           └── bootloader + its environment: persistent, NOT redundant
```

Updating means: write the entire new system into the slot you are *not* running from, tell the bootloader to try it next time, and reboot. The running system is never modified. There is no window in which the box holds half a system, because the half-written thing is the slot nobody is booting.

Three things about this layout are worth more attention than they usually get.

**The two rootfs slots are byte-identical when you ship.** Every appliance leaves the factory with the same image in both slots. That is what makes A/B work — either slot must be able to run the product. It also means **nothing device-specific can live inside a slot**: no serial number, no certificate, no per-customer configuration. This constraint comes back in Lesson 4 and dominates М12.

**The rootfs slots are read-only at runtime.** Not by convention — mounted `ro`. If the running system can write to itself, you have quietly reintroduced in-place mutation and the second slot is theatre. Everything that needs to change goes on the data partition.

**The ESP is persistent and not redundant.** The EFI System Partition holds the bootloader and the small environment file recording which slot to try. It is the one part of the disk that is neither duplicated nor disposable, which makes it the layout's most delicate piece: corrupt it and neither slot boots. RAUC's documentation is explicit that this environment must live outside the redundant partitions, precisely so a slot can be replaced without touching it.

## Step 3 — The layout, and the one number you have to choose

```
/dev/sda1   ESP        vfat    ~512M   GRUB + grubenv    ← persistent, NOT redundant
/dev/sda2   rootfs.0   ext4    ~8G     bootname=A        ← read-only at runtime
/dev/sda3   rootfs.1   ext4    ~8G     bootname=B        ← read-only at runtime
/dev/sda4   data       ext4    rest    config, container storage, spool/recordings
```

Three of those four are arithmetic. The ESP needs to hold a bootloader; 512 MB is generous and costs nothing. A root filesystem holding a kernel, a minimal userspace and your container runtime lands somewhere between 2 and 6 GB; 8 GB leaves room to grow and you must budget it **twice**.

The fourth is a product decision, and this is the moment to take it rather than discover it.

The data partition holds three things. Configuration is kilobytes. Podman's image and volume storage is a gigabyte or two, and bounded. The third is **the spool** — footage recorded but not yet uploaded — and it is not bounded by anything except the number you pick here.

You will build the spool in Lesson 4. What it needs from you now is a size, and the size answers a question in the product specification:

> *How long an uplink outage must this appliance survive without losing footage?*

The arithmetic:

```
spool bytes  =  cameras × bitrate × outage duration

8 cameras × 4 Mbit/s × 24 hours
  = 8 × 4,000,000 × 86,400 / 8
  = 345,600,000,000 bytes
  ≈ 346 GB   (322 GiB)
```

Eight cameras and one day costs a third of a terabyte. Sanity-check the shape of that before trusting it: 4 Mbit/s is 0.5 MB/s, which is 43 GB per camera per day, times eight. Yes.

So a 500 GB disk survives one day on eight cameras and not much more. A 12-hour requirement halves it. Sixteen cameras double it. **Pick the number, write it in the specification, and size the partition from it** — because the alternative is discovering your survival window in an incident report.

For this course's bench, 20 GB is plenty; you will simulate outages in minutes, not days.

## Step 4 — Build the disk

Everything from here runs on your own machine. Create a working directory and a disk image:

```bash
mkdir -p ~/edge-bench && cd ~/edge-bench
qemu-img create -f qcow2 appliance.qcow2 40G
```

`qcow2` is a sparse format: the file starts a few hundred kilobytes and grows only as you write. `ls -la` it now and again after installing — watching that number is a decent intuition-builder for how much a root filesystem actually costs.

Attach it as a network block device so you can partition it with ordinary tools:

```bash
sudo modprobe nbd max_part=8
sudo qemu-nbd --connect=/dev/nbd0 appliance.qcow2
```

Partition it:

```bash
sudo parted -s /dev/nbd0 mklabel gpt
sudo parted -s /dev/nbd0 mkpart ESP     fat32  1MiB    513MiB
sudo parted -s /dev/nbd0 set 1 esp on
sudo parted -s /dev/nbd0 mkpart rootfs0 ext4   513MiB  8705MiB
sudo parted -s /dev/nbd0 mkpart rootfs1 ext4   8705MiB 16897MiB
sudo parted -s /dev/nbd0 mkpart data    ext4   16897MiB 100%
sudo parted -s /dev/nbd0 print
```

That last command is the first thing in this module you can *see*. It should show four partitions with the sizes above, and partition 1 flagged `esp`.

Make filesystems. Note the labels — they are how you will tell the two slots apart when they are otherwise identical:

```bash
sudo mkfs.vfat -F32 -n ESP    /dev/nbd0p1
sudo mkfs.ext4 -q  -L rootfs0 /dev/nbd0p2
sudo mkfs.ext4 -q  -L rootfs1 /dev/nbd0p3
sudo mkfs.ext4 -q  -L data    /dev/nbd0p4
lsblk -f /dev/nbd0
```

`lsblk -f` should now list four filesystems with those four labels. **Take a screenshot or copy that output into your notes** — in Lesson 3 you will deliberately damage this system, and knowing exactly what healthy looked like is worth more than it sounds.

## Step 5 — Put a system in slot A, and only slot A

Install a minimal Debian into the first root slot. `debootstrap` does this without an installer:

```bash
sudo mkdir -p /mnt/slotA
sudo mount /dev/nbd0p2 /mnt/slotA
sudo debootstrap --arch=amd64 --include=linux-image-amd64,grub-efi-amd64,systemd-sysv \
     bookworm /mnt/slotA http://deb.debian.org/debian
```

That takes several minutes and downloads a few hundred megabytes. While it runs, notice what you are doing: **building a root filesystem as a directory tree**, not installing an operating system interactively. That is the shift this whole module rests on. An appliance image is an artifact you *build*, reproducibly, in CI — not a machine somebody set up once and nobody dares rebuild.

Give it a root password so you can log in, and a serial console so you can see the boot:

```bash
sudo chroot /mnt/slotA passwd root
echo 'slot A' | sudo tee /mnt/slotA/etc/slot-id
```

That `/etc/slot-id` file is a deliberate cheat and the most useful thing in this lesson. The two slots are supposed to be identical, which makes them impossible to tell apart when you are trying to work out which one booted. One line of text fixes that for the rest of the module.

Install GRUB to the ESP:

```bash
sudo mkdir -p /mnt/slotA/boot/efi
sudo mount /dev/nbd0p1 /mnt/slotA/boot/efi
for d in dev proc sys; do sudo mount --bind /$d /mnt/slotA/$d; done
sudo chroot /mnt/slotA grub-install --target=x86_64-efi --efi-directory=/boot/efi \
     --bootloader-id=BOOT --removable
```

`--removable` writes to the fallback path `EFI/BOOT/BOOTX64.EFI`, which QEMU's firmware will boot without any NVRAM entry. Real hardware usually wants a proper entry; for a bench, removable is the path of least resistance.

Now write a GRUB configuration with **one entry per slot**, by hand. Do not run `update-grub` — it generates entries for the system it is run from, which is exactly the assumption A/B breaks.

```bash
sudo tee /mnt/slotA/boot/efi/EFI/BOOT/grub.cfg > /dev/null <<'EOF'
set timeout=5
set default=0

menuentry "Slot A" {
    search --no-floppy --label rootfs0 --set root
    linux /vmlinuz root=LABEL=rootfs0 ro console=ttyS0,115200 rauc.slot=A
    initrd /initrd.img
}

menuentry "Slot B" {
    search --no-floppy --label rootfs1 --set root
    linux /vmlinuz root=LABEL=rootfs1 ro console=ttyS0,115200 rauc.slot=B
    initrd /initrd.img
}
EOF
```

Three details in there are load-bearing:

- **`root=LABEL=rootfs0`, not `/dev/sda2`.** Device names are assigned in discovery order and are not a stable identity. Labels are.
- **`ro`.** The root filesystem mounts read-only, as Step 2 insisted.
- **`rauc.slot=A`.** A kernel command-line marker naming the slot. Nothing reads it yet; Lesson 2 does.

Copy slot A into slot B, exactly as the factory would:

```bash
sudo mkdir -p /mnt/slotB
sudo mount /dev/nbd0p3 /mnt/slotB
sudo cp -a /mnt/slotA/. /mnt/slotB/
sudo umount /mnt/slotA/boot/efi          # the ESP is not part of a slot
sudo rm -rf /mnt/slotB/boot/efi/*
echo 'slot B' | sudo tee /mnt/slotB/etc/slot-id
```

Note what you just did and did not copy. Both slots got the same system. The ESP was unmounted first, because it belongs to the *box*, not to a slot — copying it into slot B would have nested a bootloader inside a root filesystem, which is a mistake worth making once in a lesson rather than once in production.

Unmount and disconnect:

```bash
for d in dev proc sys; do sudo umount /mnt/slotA/$d; done
sudo umount /mnt/slotA /mnt/slotB
sudo qemu-nbd --disconnect /dev/nbd0
```

## Step 6 — Boot it, and boot the other one

Copy the UEFI firmware (QEMU needs a writable copy of the variable store):

```bash
cp /usr/share/OVMF/OVMF_VARS.fd ./OVMF_VARS.fd
```

Boot:

```bash
qemu-system-x86_64 \
  -m 2048 -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=./OVMF_VARS.fd \
  -drive file=appliance.qcow2,format=qcow2,if=virtio \
  -nographic
```

Add `-enable-kvm` if Step 1 said you could. `-nographic` puts the serial console in your terminal, which is why the kernel command line has `console=ttyS0,115200`. **To quit QEMU from `-nographic`, press `Ctrl-a` then `x`.** Write that down; it is not guessable.

You should see GRUB's menu with two entries, then a Debian boot. Log in as root and ask the only question that matters:

```bash
cat /etc/slot-id      # -> slot A
findmnt /             # -> /dev/vda2, and note "ro" in the options
```

Now reboot, and at the GRUB menu press the down arrow and select **Slot B**:

```bash
cat /etc/slot-id      # -> slot B
findmnt /             # -> /dev/vda3
```

**That is the deliverable.** One disk, two complete systems, either one bootable, chosen at boot time. Everything in the next three lessons is about making that choice automatic, safe, and driven by a signed artifact rather than a human at a menu.

Before you move on, prove the slots are genuinely independent. From slot B:

```bash
touch /tmp/nothing            # works: tmpfs
touch /marker                 # fails: read-only file system
```

That second failure is the design working. If it succeeded, your `ro` did not take, and Lesson 3's rollback would have nothing to roll back to.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `qemu-nbd: Failed to open` or `/dev/nbd0` missing | The `nbd` module is not loaded — rerun `sudo modprobe nbd max_part=8`. Some distributions need `sudo apt install qemu-utils` for `qemu-nbd` itself. |
| `parted` warns about alignment | Harmless here, but start the first partition at 1 MiB as shown, which is what silences it and is correct on real flash anyway. |
| `debootstrap: command not found` | `sudo apt install debootstrap`. On non-Debian hosts, use a Debian container to run it, or substitute your distribution's equivalent bootstrap tool. |
| Boots to a UEFI shell instead of GRUB | The firmware could not find `EFI/BOOT/BOOTX64.EFI`. Confirm you used `--removable`, and check the ESP is really FAT32 and really flagged `esp`. |
| GRUB appears but "you need to load the kernel first" | `search --label` did not find the slot. Verify labels with `lsblk -f`, and confirm `/vmlinuz` and `/initrd.img` exist in the slot root (Debian creates these symlinks; some minimal installs do not). |
| Kernel panics with "unable to mount root fs" | Almost always the label. Boot the other slot and compare `lsblk -f` output to your `grub.cfg`. |
| Nothing appears at all with `-nographic` | The kernel is talking to a graphical console. Confirm `console=ttyS0,115200` is on the `linux` line in `grub.cfg`. |
| Cannot exit QEMU | `Ctrl-a` then `x`. |

## Recap

- In-place updates pass through states that are neither the old nor the new system. On a box nobody can visit, an interruption in one of those states is a truck roll.
- A/B keeps two complete root filesystems, writes updates into the inactive one, and switches by rebooting. There is never a partially-updated running system.
- Both slots ship byte-identical, so **nothing device-specific can live in a slot**. That single constraint shapes the rest of this module and all of М12.
- The rootfs slots mount read-only; everything mutable lives on the data partition. The ESP is persistent and not redundant, which makes it the most delicate part of the layout.
- The data partition is sized from a stated requirement — *how long an uplink outage must we survive?* — because it holds the spool. Eight cameras at 4 Mbit/s for 24 hours is about 346 GB.
- You now have a bench: one disk, two slots, either bootable, and an `/etc/slot-id` file so you always know which one you are in.

## Exercises

1. Work out the data partition size for **16 cameras at 6 Mbit/s surviving 12 hours**, then again for **4 cameras at 2 Mbit/s surviving 7 days**. One of those two is a much larger disk than intuition suggests — decide which before calculating, then see whether you were right.
2. Boot slot A and try `touch /marker`. Explain in one sentence why this failing is the design working rather than a misconfiguration.
3. Mount the data partition inside the running VM (`mount /dev/vda4 /mnt`) and confirm it is writable from **both** slots. This is the only part of the disk with that property; be able to say why.
4. Deliberately corrupt slot B — boot slot A and run `dd if=/dev/zero of=/dev/vda3 bs=1M count=50` — then reboot and try to boot B. Watch it fail, then boot A and confirm it is completely unaffected. You have just performed, by hand, the failure that Lesson 3 makes the system handle automatically.
5. Look at `ls -la appliance.qcow2` and compare it to the 40 GB you asked for. Explain the difference, and predict what happens to that number after Lesson 2 writes a second full system into slot B.

## Where this is going

You have two slots and a menu. Choosing between them is currently a human pressing a down arrow, which is exactly what an appliance cannot rely on.

Lesson 2 replaces the human with **RAUC**: a configuration describing the slots, a signed bundle format for shipping a new system, and a signature check that is not optional. You will build a certificate authority with real `openssl`, sign a bundle with it, and prove — by watching it fail — that a bundle signed by anybody else is refused.
