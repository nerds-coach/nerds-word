#!/usr/bin/env bash
# Publishes a folder of CI output (logs, screenshots, generated files) as the branch
# ci-output/<name>, replacing what was there, so it can be read with plain git.
#
#   .github/scripts/publish-ci-output.sh <name> <folder>      (needs GH_TOKEN with contents: write)
set -euo pipefail
name="$1"
dir="$2"
if [ ! -d "$dir" ] || [ -z "$(ls -A "$dir")" ]; then
  echo "nothing to publish in $dir"
  exit 0
fi
tmp="$(mktemp -d)"
cp -R "$dir"/. "$tmp"/
cd "$tmp"
printf 'run: %s/%s/actions/runs/%s\ncommit: %s\njob: %s\n' \
  "$GITHUB_SERVER_URL" "$GITHUB_REPOSITORY" "$GITHUB_RUN_ID" "$GITHUB_SHA" "$name" >RUN.txt
git init -q
git add -A
git -c user.name="NERDS CI" -c user.email="ci@users.noreply.github.com" commit -q -m "CI output: $name (${GITHUB_SHA::9})"
git push -q -f "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "HEAD:refs/heads/ci-output/$name"
echo "published ci-output/$name"
