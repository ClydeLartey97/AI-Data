# AI Energy zero-setup NVIDIA measurement launcher for Windows.
# Run in PowerShell; Python, Visual Studio and the CUDA toolkit are not needed.
$ErrorActionPreference = "Stop"

$RawCollector = "https://raw.githubusercontent.com/ClydeLartey97/AI-Data/main/grid-aware-scheduler/scripts/measure_this_pc.py"
$UvInstaller = "https://astral.sh/uv/0.12.9/install.ps1"
$WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ai-energy-dell-" + [guid]::NewGuid().ToString("N"))
$Desktop = [Environment]::GetFolderPath("Desktop")

if (-not (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue)) {
    $Graphics = @(Get-CimInstance Win32_VideoController | ForEach-Object {
        [ordered]@{
            name = $_.Name
            driver_version = $_.DriverVersion
            video_processor = $_.VideoProcessor
        }
    })
    $Processor = Get-CimInstance Win32_Processor | Select-Object -First 1
    $Inventory = [ordered]@{
        schema = "ai-energy-hardware-measurement-v1"
        collector_version = "2.0"
        measurement_id = [guid]::NewGuid().ToString()
        status = "rejected"
        platform = "windows-inventory-only"
        captured_at = [DateTime]::UtcNow.ToString("o")
        host = [ordered]@{
            os = (Get-CimInstance Win32_OperatingSystem).Caption
            cpu = $Processor.Name
            logical_cores = $Processor.NumberOfLogicalProcessors
            graphics = $Graphics
        }
        validation = [ordered]@{
            accepted = $false
            stage = "preflight"
            blockers = @("nvidia-smi was not found; no supported NVIDIA measurement path is available")
            cautions = @("Install the normal NVIDIA display driver if this computer has an NVIDIA GPU")
            eligible_metrics = [ordered]@{ throughput = $false; energy = $false }
        }
        summary = [ordered]@{}
        runs = @()
    }
    $Stamp = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss")
    $InventoryPath = Join-Path $Desktop ("measurement-windows-inventory-" + $Stamp + ".json")
    $InventoryJson = $Inventory | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText(
        $InventoryPath, $InventoryJson, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "REJECTED: no supported NVIDIA driver was found."
    Write-Host "The computer inventory was still saved to: $InventoryPath"
    Write-Host "Send back that JSON file; it identifies which measurement path is needed."
    exit 2
}

New-Item -ItemType Directory -Path $WorkDir | Out-Null
try {
    $LocalCollector = Join-Path $PSScriptRoot "measure_this_pc.py"
    if (Test-Path $LocalCollector) {
        $Collector = $LocalCollector
    } else {
        $Collector = Join-Path $WorkDir "measure_this_pc.py"
        Write-Host "Downloading the AI Energy NVIDIA collector..."
        Invoke-WebRequest -UseBasicParsing -Uri $RawCollector -OutFile $Collector
    }

    Write-Host "Preparing a temporary measurement runtime (no Python or coding tools needed)..."
    $Installer = Join-Path $WorkDir "install-uv.ps1"
    Invoke-WebRequest -UseBasicParsing -Uri $UvInstaller -OutFile $Installer
    $env:UV_UNMANAGED_INSTALL = Join-Path $WorkDir "uv-bin"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The temporary runtime installer failed." }
    $Uv = Join-Path $env:UV_UNMANAGED_INSTALL "uv.exe"
    if (-not (Test-Path $Uv)) { throw "The temporary runtime could not be started." }

    $env:UV_CACHE_DIR = Join-Path $WorkDir "uv-cache"
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $WorkDir "python"
    $env:UV_NO_PROGRESS = "1"
    $env:UV_NO_CONFIG = "1"
    $env:AI_ENERGY_BENCH_VENV = "zero-setup-launcher"
    Write-Host "Checking whether the Dell is ready for a trustworthy run..."
    & $Uv run --no-project --python 3.12 --with psutil $Collector --check-only --output $Desktop
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "No benchmark was run and PyTorch was not downloaded."
        Write-Host "Follow the message above, then run this launcher again."
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Downloading temporary NVIDIA measurement libraries (several GB)..."
    Write-Host "This may take a while. Leave other applications closed."
    & $Uv run --no-project --python 3.12 --with psutil --with torch --torch-backend auto $Collector --output $Desktop
    $MeasurementStatus = $LASTEXITCODE
    Write-Host ""
    Write-Host "Temporary runtime removed. Only the result on the Desktop remains."
    exit $MeasurementStatus
}
catch {
    Write-Host "Measurement setup failed: $($_.Exception.Message)"
    exit 1
}
finally {
    if (Test-Path $WorkDir) {
        Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
