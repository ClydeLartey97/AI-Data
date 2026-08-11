#!/bin/bash
# Overnight device characterisation. Start it, go to bed, read it in the morning.
#
# Double-click from Finder. Leave the machine PLUGGED IN and, if you close the
# lid, connected to an external display or it will sleep regardless of
# caffeinate. Results are written continuously, so a crash at hour three still
# leaves hours one and two on disk.
#
# What this produces that a short benchmark cannot: the rate the device still
# holds once it is hot. On a fanless part that is a different number from the
# one it peaks at, and it is the number a scheduler actually needs, because
# jobs run for hours rather than seconds.

set -uo pipefail

PROJECT="/Users/clydelartey/Desktop/AI Energy"
PYTHON="$HOME/venvs/national-grid/bin/python"
STAMP="$(date +%Y%m%d-%H%M)"
OUT="$PROJECT/grid-aware-scheduler/data/cache/campaign-$STAMP.json"
LOG="$PROJECT/grid-aware-scheduler/data/cache/campaign-$STAMP.log"
APPS=("Visual Studio Code" "Google Chrome" "Arc" "Claude")

# Phase length x operations x repeats, plus cool-down between each.
PHASE_MINUTES=${PHASE_MINUTES:-25}
COOLDOWN_MINUTES=${COOLDOWN_MINUTES:-8}
REPEATS=${REPEATS:-3}

cd "$PROJECT/grid-aware-scheduler" || { echo "Project folder not found"; exit 1; }

TOTAL=$(echo "($PHASE_MINUTES + $COOLDOWN_MINUTES) * 2 * $REPEATS" | bc)
echo "──────────────────────────────────────────────────────────────"
echo " Overnight sustained characterisation"
echo "──────────────────────────────────────────────────────────────"
echo
echo " Phases:    2 operations x $REPEATS repeats at ${PHASE_MINUTES} min each"
echo " Cooldown:  ${COOLDOWN_MINUTES} min between phases"
echo " Estimated: about $TOTAL minutes (~$(echo "scale=1; $TOTAL/60" | bc) hours)"
echo " Output:    $OUT"
echo
echo " This will quit: ${APPS[*]}"
echo " Keep the machine on mains power for the whole run."
echo
read -r -p "Press return to start, or Ctrl-C to cancel. " _

for app in "${APPS[@]}"; do
  if osascript -e "application \"$app\" is running" 2>/dev/null | grep -q true; then
    echo "  quitting $app"
    osascript -e "quit app \"$app\"" >/dev/null 2>&1
  fi
done
sleep 20

"$PYTHON" - <<'PY'
from hardware.telemetry import TelemetryCollector
c = TelemetryCollector()
live = c.snapshot()["devices"][0]["live"]
battery = c.battery()
print(f"  free memory {live.get('memory_available_gb')} GB"
      f" | swap {live.get('swap_used_gb')} GB"
      f" | mains power: {battery.get('on_ac_power')}")
PY

echo
echo "Starting at $(date '+%H:%M:%S'). Log: $LOG"
echo "Keeping the machine awake for the duration…"
echo

# caffeinate -i prevents idle sleep; -s prevents sleep on mains power. The
# benchmark runs as its child, so the assertion is released when it exits.
caffeinate -i -s "$PYTHON" -m hardware.campaign \
  --output "$OUT" \
  --phase-minutes "$PHASE_MINUTES" \
  --cooldown-minutes "$COOLDOWN_MINUTES" \
  --repeats "$REPEATS" 2>&1 | tee "$LOG"

status=${PIPESTATUS[0]}
echo
echo "──────────────────────────────────────────────────────────────"
if [ "$status" -ne 0 ]; then
  echo " The campaign exited early (status $status)."
  echo " Partial results were still written to:"
  echo "   $OUT"
else
  echo " Finished at $(date '+%H:%M:%S')."
fi
echo "──────────────────────────────────────────────────────────────"

echo
echo "Reopening your editor…"
open -a "Visual Studio Code" "$PROJECT"
echo
echo "Done. This window can be closed."
