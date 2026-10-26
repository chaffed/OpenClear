#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed
#
# Creates the key that keeps the ZAP scan's evidence private (plans/SCAN_EVIDENCE_PLAN.md SE1):
#   1. checks the YubiKey's PIV PIN and PUK aren't the factory defaults (offers to change them);
#   2. generates an age key in a free retired PIV slot on the YubiKey (never --force: a slot
#      already in use is left alone);
#   3. generates the paper backup key and shows it once, to write down or print; it's never saved;
#   4. generates the scan key and writes it only encrypted to the YubiKey and the paper key
#      (.github/scan-key.age); the unencrypted scan key exists only in a pipe, never on disk;
#   5. writes the scan key's public half to .github/scan-recipients.txt, for CI to encrypt to;
#   6. checks that the YubiKey alone, and the paper key alone, each decrypt it.
# Run it yourself, in a terminal, with the YubiKey plugged in: it asks for a touch and the PIN.
#
#   scripts/scan_key_setup.sh SERIAL
#
# Needs age, age-plugin-yubikey and ykman (brew install age age-plugin-yubikey ykman).
set -euo pipefail
cd "$(dirname "$0")/.."

[[ $# -eq 1 ]] || { echo "usage: $0 SERIAL   (ykman list --serials)"; exit 2; }
serial=$1
for tool in age age-keygen age-plugin-yubikey ykman; do
  command -v "$tool" >/dev/null || { echo "$tool isn't installed: brew install age age-plugin-yubikey ykman"; exit 1; }
done
[[ -e .github/scan-key.age ]] && { echo ".github/scan-key.age already exists: this sets up a new key only."; exit 1; }
ykman list --serials | grep -qx "$serial" || { echo "YubiKey $serial isn't plugged in."; exit 1; }

confirm() { read -r -p "$1 [y/N] " reply; [[ $reply == [yY] ]]; }

echo "== 1. The YubiKey's PIV PIN and PUK"
info=$(ykman --device "$serial" piv info 2>/dev/null)
grep -E "^Slot " <<<"$info" | sed 's/^/   in use: /' || true
if grep -q "default PIN" <<<"$info"; then
  echo "   Its PIV PIN is the factory default (123456)."
  if confirm "   Change it now? (Any certificate already on this key will then use the new PIN.)"; then
    ykman --device "$serial" piv access change-pin
  else
    echo "   Not changed: anyone holding this YubiKey could use the scan key. Stopping."; exit 1
  fi
fi
if grep -q "default PUK" <<<"$info"; then
  echo "   Its PIV PUK is the factory default (12345678); it unblocks a locked PIN."
  if confirm "   Change it now?"; then
    ykman --device "$serial" piv access change-puk
  else
    echo "   Not changed: the default PUK lets anyone reset the PIN. Stopping."; exit 1
  fi
fi

echo "== 2. An age key on the YubiKey (a touch on each use, the PIN once per session)"
echo "   If its management key is the default, age-plugin-yubikey replaces it with a random one"
echo "   protected by the PIN."
confirm "   Go ahead?" || exit 1
echo "   Touch the YubiKey when it flashes."
# It prints the identity stub (which slot on which YubiKey; no secret), with the recipient in a comment.
stub=$(age-plugin-yubikey --generate --serial "$serial" --name "OpenClear scan evidence" \
  --pin-policy once --touch-policy always)
yubikey=$(grep -o "age1yubikey1[0-9a-z]*" <<<"$stub" | head -1)
[[ -n $yubikey ]] || { echo "   Couldn't read the new key's recipient from the YubiKey."; exit 1; }
echo "   recipient: $yubikey"

echo "== 3. The paper backup key"
paper_identity=$(age-keygen 2>/dev/null | grep "^AGE-SECRET-KEY-")
paper=$(age-keygen -y <<<"$paper_identity")
cat <<EOF

   Write this down or print it, check it, and keep it somewhere safe and apart from the YubiKey.
   It's shown once and saved nowhere; with it alone anyone can read the scan's evidence.

       $paper_identity

EOF
read -r -p "   Press Enter when it's written down; the screen is then cleared."
clear

echo "== 4. The scan key, encrypted to the YubiKey and the paper key"
public=$(mktemp)
trap 'rm -f "$public"' EXIT
age-keygen 2>"$public" | age -r "$yubikey" -r "$paper" -o .github/scan-key.age
grep -o "age1[0-9a-z]*" "$public" > .github/scan-recipients.txt
expected=$(cat .github/scan-recipients.txt)
echo "   wrote .github/scan-key.age and .github/scan-recipients.txt: $expected"

echo "== 5. Each decrypts it on its own"
echo "-- The YubiKey (touch it when it flashes)"
derived=$(age -d -i <(printf '%s\n' "$stub") .github/scan-key.age | age-keygen -y)
[[ $derived == "$expected" ]] && echo "   YubiKey $serial: decrypts" || { echo "   YubiKey $serial: FAILED"; exit 1; }
echo "-- The paper key: type it from the paper (it isn't shown)"
read -r -s -p "   AGE-SECRET-KEY-... " typed; echo
derived=$(age -d -i <(printf '%s\n' "$typed") .github/scan-key.age | age-keygen -y) || true
unset typed paper_identity
[[ $derived == "$expected" ]] && echo "   paper key: decrypts" || { echo "   paper key: FAILED (a typo on the paper?)"; exit 1; }
echo "Done. Commit .github/scan-key.age and .github/scan-recipients.txt."
