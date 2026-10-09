#!/usr/bin/env bash
# Mirrors each SDK from sdks/ (the source of truth, generated from this repo's API contract)
# to its own repository, with history (git subtree split). Run from the repo root on main:
#
#   scripts/split_sdks.sh            # all four
#   scripts/split_sdks.sh python     # just one
#
# Then tag the mirror (v0.1.0) to publish: each mirror's .github/workflows/release.yml does
# the rest.
set -euo pipefail

repo_for() {
  case "$1" in
    python) echo chaffed/openclear-sdk-python ;;
    typescript) echo chaffed/openclear-sdk-js ;;
    dotnet) echo chaffed/openclear-sdk-dotnet ;;
    embed) echo chaffed/openclear-embed ;;
    *) echo "unknown SDK: $1" >&2; exit 2 ;;
  esac
}

for name in ${@:-python typescript dotnet embed}; do
  repo=$(repo_for "$name")
  branch="split/sdk-$name"
  git subtree split --prefix="sdks/$name" -b "$branch" >/dev/null
  git push --force "https://github.com/$repo.git" "$branch:main"
  git branch -D "$branch" >/dev/null
  echo "sdks/$name -> $repo"
done
