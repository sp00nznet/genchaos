<#
  General Chaos recomp setup (Windows). Run through Setup.cmd.

  Runs exactly the steps in README "Step by step", skipping any already done,
  so a rerun after a failure resumes. Asks before installing anything. Uses
  your own ROM; nothing is downloaded or bundled. Details go to setup.log.

  -Yes   answer yes to every install prompt (unattended)
  -Rom   path to your General Chaos ROM (otherwise found or asked for)
#>
param([switch]$Yes, [string]$Rom = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $Root "setup.log"
$Build = Join-Path $Root "build"
$Genrecomp = Join-Path (Split-Path -Parent $Root) "genrecomp"
$GenrecompUrl = "https://github.com/sp00nznet/genrecomp.git"
$GenrecompPin = "b390007"   # genrecomp master with #6 and #7
"genchaos setup $(Get-Date -Format s)" | Out-File $Log -Encoding utf8

function Say($msg) { Write-Host $msg; $msg | Out-File $Log -Append -Encoding utf8 }
function Fail($msg) {
    Write-Host ""
    Write-Host "Setup stopped: $msg" -ForegroundColor Red
    Write-Host "Details are in $Log"
    exit 1
}
function Run($what, $exe, $argList) {
    "`n> $exe $argList" | Out-File $Log -Append -Encoding utf8
    $p = Start-Process -FilePath $exe -ArgumentList $argList -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput "$Log.out" -RedirectStandardError "$Log.err"
    Get-Content "$Log.out", "$Log.err" -ErrorAction SilentlyContinue | Out-File $Log -Append -Encoding utf8
    Remove-Item "$Log.out", "$Log.err" -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { Fail "$what failed (exit $($p.ExitCode))." }
}
function Ask($question) {
    if ($Yes) { return $true }
    return (Read-Host "$question [y/N]") -match '^[yY]'
}
function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

# 1. Prerequisites ---------------------------------------------------------
Say "1/6 Checking prerequisites"
foreach ($t in @(@("git", "Git.Git", "Git", "60 MB"), @("cmake", "Kitware.CMake", "CMake", "40 MB"))) {
    if (-not (Have $t[0])) {
        if (Ask "$($t[2]) is missing. Install it with winget (about $($t[3]))?") {
            Run "Installing $($t[2])" "winget" "install --id $($t[1]) -e --silent --accept-package-agreements --accept-source-agreements"
            Fail "$($t[2]) was installed. Close this window and run Setup.cmd again so the new PATH is picked up."
        } else { Fail "$($t[2]) is required. Install it and run Setup.cmd again." }
    }
}
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = if (Test-Path $vswhere) { & $vswhere -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath } else { $null }
if (-not $vs) {
    Fail "Visual Studio 2022 with 'Desktop development with C++' is required (free Community edition: https://visualstudio.microsoft.com). Install it, then run Setup.cmd again."
}

# Python: the py launcher, not the Microsoft Store "python" alias, which
# opens the Store instead of running anything
$Py = $null
if (Have py) { $Py = "py"; $PyArgs = "-3" }
elseif ((Have python) -and ((Get-Command python).Source -notlike "*WindowsApps*")) { $Py = "python"; $PyArgs = "" }
if (-not $Py) {
    if (Ask "Python 3 is missing. Install it with winget (about 30 MB)?") {
        Run "Installing Python" "winget" "install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements"
        Fail "Python was installed. Close this window and run Setup.cmd again so the new PATH is picked up."
    } else { Fail "Python 3 is required to generate the source. Install it from https://python.org and run Setup.cmd again." }
}
& $Py $PyArgs -c "import capstone" 2>$null
if ($LASTEXITCODE -ne 0) {
    if (Ask "The Python package 'capstone' (the disassembler, about 3 MB) is missing. Install it with pip?") {
        Run "Installing capstone" $Py "$PyArgs -m pip install --user capstone"
    } else { Fail "capstone is required: '$Py $PyArgs -m pip install capstone', then run Setup.cmd again." }
}

$Vcpkg = if ($env:VCPKG_ROOT) { $env:VCPKG_ROOT } else { "C:\vcpkg" }
if (-not (Test-Path "$Vcpkg\vcpkg.exe")) {
    if (Ask "vcpkg (used for SDL2) is not at $Vcpkg. Install it there (about 100 MB)?") {
        Run "Downloading vcpkg" "git" "clone https://github.com/microsoft/vcpkg `"$Vcpkg`""
        Run "Bootstrapping vcpkg" "$Vcpkg\bootstrap-vcpkg.bat" "-disableMetrics"
    } else { Fail "SDL2 comes from vcpkg. Set VCPKG_ROOT to an existing vcpkg, or allow the install." }
}
if (-not (Test-Path "$Vcpkg\installed\x64-windows\share\sdl2")) {
    if (Ask "SDL2 is not installed in vcpkg. Build it now (a few minutes, about 50 MB)?") {
        Run "Installing SDL2" "$Vcpkg\vcpkg.exe" "install sdl2:x64-windows"
    } else { Fail "SDL2 is required: run '$Vcpkg\vcpkg.exe install sdl2:x64-windows' and then Setup.cmd again." }
}
Say "    git, cmake, Visual Studio, Python + capstone, SDL2 found"

# 2. genrecomp beside this folder ------------------------------------------
Say "2/6 genrecomp"
if (-not (Test-Path "$Genrecomp\CMakeLists.txt")) {
    if (Ask "genrecomp (the Genesis runtime this builds on) is not at $Genrecomp. Download it there (about 20 MB)?") {
        Run "Downloading genrecomp" "git" "clone $GenrecompUrl `"$Genrecomp`""
        Run "Pinning genrecomp" "git" "-C `"$Genrecomp`" checkout $GenrecompPin"
    } else { Fail "genrecomp must be checked out at $Genrecomp." }
}
if (-not (Test-Path "$Genrecomp\ext\Genesis-Plus-GX\core\system.c")) {
    Run "Fetching Genesis Plus GX" "git" "-C `"$Genrecomp`" submodule update --init"
}
Say "    $Genrecomp ready"

# 3. Your ROM --------------------------------------------------------------
Say "3/6 Your ROM"
if (-not $Rom) {
    $found = Get-ChildItem $Root -File | Where-Object { $_.Extension -in ".gen", ".bin", ".smd" } | Select-Object -First 1
    if ($found) { $Rom = $found.FullName }
    elseif (-not $Yes) { $Rom = (Read-Host "Drag your General Chaos ROM (.gen/.bin, unzipped) into this window and press Enter").Trim('"', ' ') }
}
if (-not $Rom -or -not (Test-Path $Rom)) { Fail "No ROM found. Put your General Chaos ROM (unzipped, .gen or .bin) in this folder, or pass -Rom." }
$bytes = [System.IO.File]::ReadAllBytes($Rom)
$title = [System.Text.Encoding]::ASCII.GetString($bytes, 0x150, 13)
if ($bytes.Length -ne 1048576 -or $title -ne "General Chaos") {
    Fail "$Rom isn't the General Chaos ROM this recomp is built from (expected a 1 MB ROM titled General Chaos; interleaved .smd dumps aren't supported)."
}
Say "    using $Rom"

# 4. Generate source from the ROM ------------------------------------------
Say "4/6 Generating C from your ROM (about 10 seconds)"
if (-not (Test-Path "$Root\src\recomp\recomp_funcs.h")) {
    Run "Generating source" $Py "$PyArgs `"$Genrecomp\tools\recompiler\generate.py`" `"$Rom`" -o `"$Root\src\recomp`" -c `"$Root\recomp.json`""
}
Say "    src\recomp ready"

# 5. Build -----------------------------------------------------------------
Say "5/6 Building (first build takes a few minutes)"
if (-not (Test-Path "$Build\CMakeCache.txt")) {
    Run "Configuring" "cmake" "-S `"$Root`" -B `"$Build`" -A x64 -DCMAKE_TOOLCHAIN_FILE=`"$Vcpkg\scripts\buildsystems\vcpkg.cmake`""
}
Run "Building" "cmake" "--build `"$Build`" --config Release"
Run "The smoke test" "$Build\Release\genchaos.exe" "--headless --frames 600 `"$Rom`""
Say "    built; 600 frames ran headless"

# 6. Launcher --------------------------------------------------------------
Say "6/6 Launcher"
@"
@echo off
rem Plays General Chaos, recompiled. Arrows, Z/X/C = A/B/C, Enter = Start, Esc = quit.
"%~dp0build\Release\genchaos.exe" %* "$Rom"
"@ | Out-File (Join-Path $Root "Play General Chaos.cmd") -Encoding ascii
Say "    created 'Play General Chaos.cmd'"

Write-Host ""
Write-Host "Done. Double-click 'Play General Chaos.cmd' to play." -ForegroundColor Green
