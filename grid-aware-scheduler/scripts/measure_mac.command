#!/bin/sh
# AI Energy zero-setup Apple-silicon measurement launcher.
# Requires only the curl and shell programs included with macOS.
set -u

RAW_COLLECTOR="https://raw.githubusercontent.com/ClydeLartey97/AI-Data/main/grid-aware-scheduler/scripts/measure_this_mac.py"
UV_INSTALLER="https://astral.sh/uv/0.12.9/install.sh"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "REJECTED: this launcher only measures Apple-silicon Macs."
    exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
    echo "REJECTED: macOS curl was not found."
    exit 2
fi
macos_major="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$macos_major" -lt 14 ]; then
    echo "REJECTED: macOS 14 or newer is required by the Apple GPU measurement library."
    exit 2
fi

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/ai-energy-mac.XXXXXX")" || exit 1
cleanup() {
    rm -rf -- "$work_dir"
}
trap cleanup EXIT HUP INT TERM

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -f "$script_dir/measure_this_mac.py" ]; then
    collector="$script_dir/measure_this_mac.py"
else
    collector="$work_dir/measure_this_mac.py"
    echo "Downloading the AI Energy Mac collector..."
    curl -fsSL "$RAW_COLLECTOR" -o "$collector" || {
        echo "Could not download the collector. Check the internet connection and try again."
        exit 1
    }
fi

echo "Preparing a temporary measurement runtime (no Xcode or Python needed)..."
installer="$work_dir/install-uv.sh"
curl -fsSL "$UV_INSTALLER" -o "$installer" || {
    echo "Could not download the temporary runtime. Check the internet connection and try again."
    exit 1
}
UV_UNMANAGED_INSTALL="$work_dir/uv-bin" sh "$installer" >/dev/null || exit 1
uv="$work_dir/uv-bin/uv"
if [ ! -x "$uv" ]; then
    echo "The temporary runtime could not be started."
    exit 1
fi

export UV_CACHE_DIR="$work_dir/uv-cache"
export UV_PYTHON_INSTALL_DIR="$work_dir/python"
export UV_NO_PROGRESS=1
export UV_NO_CONFIG=1
export AI_ENERGY_BENCH_VENV="zero-setup-launcher"
desktop="$HOME/Desktop"

echo "Checking whether the Mac is ready for a trustworthy run..."
"$uv" run --no-project --python 3.12 --with psutil \
    "$collector" --check-only --output "$desktop"
preflight_status=$?
if [ "$preflight_status" -ne 0 ]; then
    echo ""
    echo "No benchmark was run and no measurement library was downloaded."
    echo "Follow the message above, then run this launcher again."
    exit "$preflight_status"
fi

echo ""
echo "Downloading the temporary Apple GPU measurement library..."
echo "This may take a few minutes. Keep the Mac plugged in and leave other apps closed."
"$uv" run --no-project --python 3.12 --with psutil --with 'mlx==0.32.2' \
    "$collector" --output "$desktop"
measurement_status=$?
echo ""
echo "Temporary runtime removed. Only the result on the Desktop remains."
exit "$measurement_status"
