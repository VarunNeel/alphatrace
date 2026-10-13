#!/data/data/com.termux/files/usr/bin/bash
# Fetch the latest NSE bhavcopy day(s) and, if this folder is a git repo, push them to GitHub.
cd "$(dirname "$0")" || exit 1
echo "== $(date) =="
python fetch_eod.py --days 5 || exit 1
if [ -d .git ]; then
  git pull --rebase -q origin main 2>/dev/null
  git add eod
  git commit -qm "eod $(date +%F)" 2>/dev/null && git push -q origin main && echo "pushed"
fi
