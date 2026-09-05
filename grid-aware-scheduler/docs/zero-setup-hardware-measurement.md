# Zero-setup hardware measurement

These launchers collect an empirical hardware anchor without requiring Python,
Xcode, Homebrew, Visual Studio or a CUDA toolkit. They use a temporary runtime,
delete it when finished, and leave one JSON result on the Desktop.

## Before either run

1. Restart the machine if it has been heavily used.
2. Connect it to mains power and give it five minutes to cool.
3. Quit browsers, games, video tools, model runners and other applications.
4. Keep the terminal open until the launcher says it has finished.

The collector checks free memory, swap, CPU load and thermal conditions. The
NVIDIA version also checks GPU load, temperature and other CUDA processes. A
failed check exits before downloading the large GPU library. A completed run
is rejected if its three repeats differ by more than 5%.

## M1 Ultra Mac Studio

Open Terminal and paste this as one line:

```sh
curl -fsSL https://raw.githubusercontent.com/ClydeLartey97/AI-Data/main/grid-aware-scheduler/scripts/measure_mac.command -o ~/Desktop/measure_mac.command && bash ~/Desktop/measure_mac.command
```

The result is named `measurement-apple-m1-ultra-<time>.json`. Send back the
JSON file, not a screenshot. The file records whether the machine has the
48-core or 64-core GPU; the collector refuses the run if macOS does not expose
that distinction.

This measures GPU compute and shared-memory throughput. It does **not** measure
the Neural Engine, and it does not claim whole-machine watts: unprivileged
macOS does not expose a sufficiently reliable Mac Studio power reading. A
plug-in wall meter can later add outlet-level power, which is the figure that
matters for electricity cost.

## NVIDIA Dell

The normal NVIDIA display driver must already be installed; `nvidia-smi` comes
with that driver. No separate CUDA toolkit is needed. Open PowerShell and paste
this as one line:

```powershell
$s="$env:USERPROFILE\Desktop\measure_dell.ps1"; Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/ClydeLartey97/AI-Data/main/grid-aware-scheduler/scripts/measure_dell.ps1 -OutFile $s; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $s
```

The result is named for the detected NVIDIA card. It includes measured GPU
compute, memory throughput and `nvidia-smi` board power. Board power excludes
the CPU, RAM, fans and power-supply losses, so it must not be presented as the
facility or wall-socket draw.

If the Dell does not have a usable NVIDIA driver, the launcher still writes a
small `measurement-windows-inventory-<time>.json` using Windows' own hardware
inventory. That file is rejected as benchmark evidence, but identifies the GPU
without requiring any coding tools so the correct AMD, Intel or driver path can
be added deliberately rather than guessed.

## Reading the result

- `status: "accepted"` means the microbenchmark passed the preflight and
  repeatability gates and can be reviewed for calibration.
- `status: "rejected"` is diagnostic only. Follow the listed blockers and run
  the launcher again; never add a rejected result to the hardware catalogue.
- A single machine measurement is a hardware anchor, not proof of savings at
  data-centre scale. Fleet telemetry, job progress/SLA data, electricity
  signals and facility meters are still required for an operator pilot.

## Importing a returned result

From the project folder, import an accepted file with:

```sh
~/venvs/national-grid/bin/python scripts/import_hardware_measurement.py ~/Desktop/measurement-apple-m1-ultra-YYYYMMDD-HHMMSS.json
```

The importer refuses rejected or malformed files and appends accepted runs to
the existing baseline history. Three **separate accepted launcher runs** with
the same device and software-stack fingerprint are required before the product
establishes a reproduced hardware ceiling. The three repeats inside each run
are the stability check; they do not replace those three separate runs. The
same file cannot be imported more than once.
