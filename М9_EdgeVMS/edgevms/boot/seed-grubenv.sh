#!/bin/sh
# Lesson 18, Step 2 — run INSIDE the VM once (build-disk.sh already did it at
# build time). Both slots known good, A first. Run `grub-editenv ... list`
# after every step from here on: it is the window into the whole mechanism.
set -e
mount -o remount,rw /boot/efi 2>/dev/null || mount /dev/vda1 /boot/efi
grub-editenv /boot/efi/grubenv create
grub-editenv /boot/efi/grubenv set ORDER="A B" A_OK=1 A_TRY=0 B_OK=1 B_TRY=0
grub-editenv /boot/efi/grubenv list
