#!/bin/bash
# Capture a device baseline on a quiet machine, then put things back.
#
# Double-click this in Finder. Do NOT run it from a terminal inside the editor
# it is about to quit — Terminal.app owns this process, so the editor closing
# cannot take the benchmark down with it.
#
# The apps being closed are the ones actually competing for memory and the
# GPU. On an 8 GB part that competition is not noise: the same benchmark
# measured 80.1 GB/s on a quiet machine and 36.9 GB/s on a busy one, with
# thirteen times the run-to-run variation. A reading taken alongside a browser
# is not a baseline.

set -uo pipefail

PROJECT="/Users/clydelartey/Desktop/AI Energy"
PYTHON="$HOME/venvs/national-grid/bin/python"
RUNS=3
SETTLE_SECONDS=20
APPS=("Visual Studio Code" "Google Chrome" "Arc" "Claude")

cd "$PROJECT/grid-aware-scheduler" || { echo "Project folder not found"; exit 1; }

echo "──────────────────────────────────────────────────────────────"
echo " Device baseline capture"
echo "──────────────────────────────────────────────────────────────"
echo
echo "This will quit: ${APPS[*]}"
echo "It runs the benchmark $RUNS times, then reopens your editor."
echo "Unsaved work in those apps should be saved first."
echo
read -r -p "Press return to start, or Ctrl-C to cancel. " _

closed=()
for app in "${APPS[@]}"; do
  if pgrep -x "$app" >/dev/null 2>&1 || osascript -e "application \"$app\" is running" 2>/dev/null | grep -q true; then
    echo "  quitting $app"
    osascript -e "quit app \"$app\"" >/dev/null 2>&1
    closed+=("$app")
  fi
done

echo
echo "Waiting ${SETTLE_SECONDS}s for memory to be released…"
sleep "$SETTLE_SECONDS"

"$PYTHON" - <<'PY'
from hardware.telemetry import TelemetryCollector
live = TelemetryCollector().snapshot()["devices"][0]["live"]
print(f"  free memory now: {live.get('memory_available_gb')} GB"
      f" | swap in use: {live.get('swap_used_gb')} GB")
PY

echo
failed=0
for i in $(seq 1 "$RUNS"); do
  echo "── run $i of $RUNS ──"
  if ! "$PYTHON" -m hardware.microbench --store; then
    echo "  run $i was refused or failed (see the reason above)"
    failed=$((failed + 1))
  fi
  if [ "$i" -lt "$RUNS" ]; then
    echo "  cooling for 30s so thermal state does not carry between runs"
    sleep 30
  fi
  echo
done

if [ "${SKIP_INFERENCE:-0}" != "1" ]; then
  echo "── model throughput: prefill and decode ──"
  echo "  (set SKIP_INFERENCE=1 to leave this out)"
  for i in $(seq 1 "$RUNS"); do
    echo "  inference run $i of $RUNS"
    if ! "$PYTHON" -m hardware.inference_bench --store; then
      echo "  inference run $i was refused or failed"
      failed=$((failed + 1))
    fi
    [ "$i" -lt "$RUNS" ] && sleep 30
  done
  echo
fi

echo "──────────────────────────────────────────────────────────────"
"$PYTHON" - <<'PY'
from hardware import baseline_store
summary = baseline_store.summary()
print(f" runs recorded: {summary['run_count']}"
      f"  (validated: {summary['validated_run_count']})")
for device in summary["devices"]:
    state = baseline_store.baseline(device)
    if state["established"]:
        print(f"\n BASELINE ESTABLISHED — {device}")
        for label, values in state["metrics"].items():
            print(f"   {label:<24} {values['median']:>9}"
                  f"   spread {values['spread_percent']}% over {values['samples']} runs")
        print("\n Scope: ceiling on ideal dense work — not a workload rate.")
    else:
        print(f"\n {device}: no baseline yet — {state['reason']}")
PY
echo "──────────────────────────────────────────────────────────────"

if [ "$failed" -gt 0 ]; then
  echo
  echo "$failed run(s) did not count. If the reason was memory or a busy CPU,"
  echo "close anything still running and start this again."
fi

echo
echo "Reopening your editor…"
for app in "${closed[@]}"; do
  case "$app" in
    "Visual Studio Code") open -a "Visual Studio Code" "$PROJECT" ;;
    *) : ;;  # browsers and chat apps are left closed; reopen them yourself
  esac
done

echo
echo "Done. This window can be closed."
