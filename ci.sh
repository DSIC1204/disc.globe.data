#!/usr/bin/env bash
# Steps for the "Update DSIC feeds" workflow. Kept here so the workflow file stays short.
set -euo pipefail
BOT_NAME="dsic-feed-bot"
BOT_MAIL="41898282+github-actions[bot]@users.noreply.github.com"

case "${1:-}" in
  load)      # previous data (leadership, last good copies) from the data branch
    mkdir -p out
    if git fetch --depth=1 origin data 2>/dev/null; then git archive FETCH_HEAD | tar -x -C out; fi
    ls -la out ;;
  fetch)
    python3 dsic_update.py out ;;
  publish)   # replace the data branch with a single fresh commit (keeps the repository small)
    cd out
    git init -q -b data
    git config user.name "$BOT_NAME"; git config user.email "$BOT_MAIL"
    git add -A
    git commit -qm "feeds $(date -u +%Y-%m-%dT%H:%MZ)"
    git push -qf "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" data ;;
  keepalive) # GitHub pauses schedules after 60 days without repository activity
    last=$(git log -1 --format=%ct)
    if [ $(( $(date +%s) - last )) -gt $(( 45 * 86400 )) ]; then
      git config user.name "$BOT_NAME"; git config user.email "$BOT_MAIL"
      git commit --allow-empty -qm "keepalive"; git push -q
    fi ;;
  *) echo "usage: ci.sh load|fetch|publish|keepalive"; exit 1 ;;
esac
