#!/bin/sh
# Lesson 19, Step 3 — check Quadlet files without deploying them.
# `systemd-analyze verify` cannot: it does not know [Container] and ignores
# the file. The generator's dry-run is the tool, and it belongs in CI.
#   quadlet/check-quadlet.sh            # checks the files in this directory
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
GEN=/usr/lib/systemd/system-generators/podman-system-generator
[ -x "$GEN" ] || { echo "no $GEN here (needs podman); run this on the bench or in CI" >&2; exit 2; }
QUADLET_UNIT_DIRS="$DIR" "$GEN" --dryrun
