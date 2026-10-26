#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
#
# Reads the ZAP scan's encrypted evidence from a run (plans/SCAN_EVIDENCE_PLAN.md SE3): downloads
# the run's zap-evidence artifact, unlocks the scan key (.github/scan-key.age) with the YubiKey or,
# with --paper, the paper key, and decrypts the reports into .zap/evidence-<run>/ (git-ignored).
# The unlocked scan key stays in memory and pipes; it's never written to disk.
#
#   scripts/open_scan_evidence.sh RUN_ID            # the YubiKey: a touch, and its PIN once
#   scripts/open_scan_evidence.sh RUN_ID --paper    # the paper key, typed in (not shown)
#
# Needs gh (signed in), age, and for the YubiKey age-plugin-yubikey.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ $# -ge 1 && $# -le 2 ]] || { echo "usage: $0 RUN_ID [--paper]"; exit 2; }
run=$1
paper=${2:-}
[[ -z $paper || $paper == --paper ]] || { echo "usage: $0 RUN_ID [--paper]"; exit 2; }
[[ $run =~ ^[0-9]+$ ]] || { echo "RUN_ID is the number in the run's address."; exit 2; }

download=$(mktemp -d)
trap 'rm -rf "$download"' EXIT
gh run download "$run" --name zap-evidence --dir "$download"
[[ -f $download/zap-evidence.tar.gz.age ]] || { echo "Run $run kept no zap-evidence."; exit 1; }

if [[ -n $paper ]]; then
  read -r -s -p "Paper key (AGE-SECRET-KEY-..., not shown): " identity; echo
else
  echo "Touch the YubiKey when it flashes."
  identity=$(age-plugin-yubikey --identity 2>/dev/null)
  [[ -n $identity ]] || { echo "No YubiKey with an age key is plugged in."; exit 1; }
fi

scan_key=$(age -d -i <(printf '%s\n' "$identity") .github/scan-key.age)
unset identity
out=.zap/evidence-$run
mkdir -p "$out"
age -d -i <(printf '%s\n' "$scan_key") "$download/zap-evidence.tar.gz.age" | tar -xzf - -C "$out" --strip-components 1
unset scan_key
echo "Decrypted into $out/:"
ls "$out"
echo "The reports are JSON (ZAP's format); each alert lists its requests under \"instances\"."
