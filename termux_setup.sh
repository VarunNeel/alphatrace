#!/data/data/com.termux/files/usr/bin/bash
# AlphaTrace one-time Termux setup. Run from the folder where you unzipped the kit:  bash termux_setup.sh
set -e
echo "== 1/4 packages =="
pkg update -y && pkg install -y python git cronie termux-api
echo "== 2/4 storage access (tap Allow on the popup) =="
[ -d "$HOME/storage" ] || termux-setup-storage
sleep 2
KIT="$(pwd)"
echo "== 3/4 optional: GitHub repo for the daily feed =="
echo "Create an EMPTY public repo on GitHub first, and a token (Settings > Developer settings > Fine-grained token,"
echo "repository access = that repo, permission Contents = Read and write)."
read -p "GitHub username (blank = skip git push, local files only): " GHU
if [ -n "$GHU" ]; then
  read -p "Repo name: " GHR
  read -s -p "Token (hidden): " GHT; echo
  git config --global user.name "$GHU"; git config --global user.email "$GHU@users.noreply.github.com"
  mkdir -p "$HOME/alphatrace" && cd "$HOME/alphatrace"
  [ -d .git ] || git init -q -b main
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://$GHU:$GHT@github.com/$GHU/$GHR.git"
  cp -r "$KIT"/fetch_eod.py "$KIT"/fundamentals.py "$KIT"/yahoo_ohlcv.py "$KIT"/termux_daily.sh "$KIT"/README.md "$KIT"/.github . 2>/dev/null || true
  git add -A && git commit -qm "AlphaTrace EOD kit" || true
  git push -u origin main && echo "pushed — feed URL: https://raw.githubusercontent.com/$GHU/$GHR/main/eod/"
else
  mkdir -p "$HOME/alphatrace" && cp -r "$KIT"/fetch_eod.py "$KIT"/fundamentals.py "$KIT"/yahoo_ohlcv.py "$KIT"/termux_daily.sh "$HOME/alphatrace/"
fi
echo "== 4/4 daily schedule (6:45 pm and 9:15 pm, every weekday) =="
chmod +x "$HOME/alphatrace/termux_daily.sh"
( crontab -l 2>/dev/null | grep -v termux_daily.sh; echo "45 18 * * 1-5 $HOME/alphatrace/termux_daily.sh >> $HOME/alphatrace/daily.log 2>&1"; echo "15 21 * * 1-5 $HOME/alphatrace/termux_daily.sh >> $HOME/alphatrace/daily.log 2>&1" ) | crontab -
pgrep crond >/dev/null || crond
termux-wake-lock || true
echo
echo "Done. Test now:  bash ~/alphatrace/termux_daily.sh"
echo "Keep Termux alive: Android Settings > Apps > Termux > Battery > Unrestricted (ColorOS/OxygenOS also: allow auto-start)."
