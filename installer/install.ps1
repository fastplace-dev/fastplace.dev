# Fastplace one-line installer — Windows (PowerShell 5.1+ / pwsh).
#
#   irm https://fastplace.dev/install.ps1 | iex
#
# What it does (and everything it does):
#   1. Installs uv (the Python package manager) with uv's official
#      standalone installer, unless uv is already on PATH.
#   2. Installs the fastplace CLI into its own isolated environment:
#      uv tool install --upgrade --python 3.12 fastplace
#      (uv downloads a managed CPython 3.12; the system Python is never
#      touched). Everything lands under ~\.local — no admin required.
#   3. Makes sure ~\.local\bin is on your user PATH.
#   4. Verifies the install and prints your next step.
#
# Options: -Mcp (install the AI-agent extra), -NoModifyPath.
# Environment: FASTPLACE_VERSION=<x> pins a release.
#
# Source: https://github.com/fastplace-dev/fastplace.dev/blob/master/installer/install.ps1

param(
    # -NoModifyPath stays the public name (docs, parity with install.sh's
    # --no-modify-path); the internal variable is renamed because uv's
    # installer script sets a same-named variable in this scope.
    [Alias('NoModifyPath')]
    [switch]$SkipModifyPath,
    [switch]$Mcp
)

$ErrorActionPreference = 'Stop'

$INSTALLER_VERSION = '1.0.0'
$UvInstallUrl = 'https://astral.sh/uv/install.ps1'
$UvToolBinDir = Join-Path $env:USERPROFILE '.local\bin'

# Session PATH refresh (the registry change only reaches NEW terminals).
$env:Path = "$UvToolBinDir;$env:Path"

function Write-Step($message) { Write-Host $message -ForegroundColor DarkGray }
function Write-Info($message) { Write-Host $message -ForegroundColor Cyan }

Write-Step "fastplace installer $INSTALLER_VERSION"

# --- validate options before touching anything ------------------------------

$installSpec = 'fastplace'
if ($Mcp) { $installSpec = 'fastplace[mcp]' }

if ($env:FASTPLACE_VERSION) {
    if ($env:FASTPLACE_VERSION -notmatch '^[0-9]+(\.[0-9]+){0,2}$') {
        Write-Error "FASTPLACE_VERSION must be a release like 0.4.3, got: $env:FASTPLACE_VERSION"
        exit 2
    }
    $installSpec = "$installSpec==$($env:FASTPLACE_VERSION)"
}

# --- pre-existing install check (warn, never touch) -------------------------

$existing = Get-Command fastplace -ErrorAction SilentlyContinue
if ($existing -and $existing.Source -notlike "$UvToolBinDir*") {
    Write-Info "note: fastplace is already installed at $($existing.Source) (not managed"
    Write-Info "by this installer — pip, pipx, or a package manager put it there). This"
    Write-Info "install lands in $UvToolBinDir; whichever directory comes first on your"
    Write-Info "PATH wins when both exist. Uninstall the old one if commands behave oddly."
}

# --- 1. uv --------------------------------------------------------------------

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Step 'Installing uv (https://astral.sh)...'
    try {
        # The uv project's documented Windows bootstrap shape.
        Invoke-RestMethod $UvInstallUrl | Invoke-Expression
    }
    catch {
        Write-Error "Failed to install uv: $_. Check your connection and retry, or install uv from https://docs.astral.sh/uv/."
        exit 1
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error 'uv was installed but is not on PATH. Open a new terminal and re-run.'
    exit 1
}

# --- 2. fastplace CLI -----------------------------------------------------------

Write-Step "Installing $installSpec (managed Python 3.12, isolated environment)..."
uv tool install --upgrade --python 3.12 $installSpec
if ($LASTEXITCODE -ne 0) {
    Write-Error "uv tool install failed for $installSpec."
    exit 1
}

# --- 3. user PATH ----------------------------------------------------------------

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathModified = $false
if ($userPath -notlike "*$UvToolBinDir*") {
    if ($SkipModifyPath) {
        Write-Info "Make sure $UvToolBinDir is on your PATH (user environment variables)."
    }
    else {
        # Append user-scope; no admin needed. Same rule as the bash side's
        # rc-file line: one entry, added once.
        [Environment]::SetEnvironmentVariable(
            'Path', ($userPath.TrimEnd(';') + ';' + $UvToolBinDir), 'User')
        $pathModified = $true
    }
}

# --- 4. verify ---------------------------------------------------------------------

$fastplaceBin = Join-Path $UvToolBinDir 'fastplace.exe'
if (-not (Test-Path $fastplaceBin)) {
    $fastplaceBin = Join-Path $UvToolBinDir 'fastplace'
}
if (-not (Test-Path $fastplaceBin)) {
    Write-Error "fastplace was installed but $fastplaceBin is missing."
    exit 1
}
$installedVersion = & $fastplaceBin --version

# --- 5. next steps -------------------------------------------------------------------

Write-Host ''
Write-Info 'Fastplace is ready.'
Write-Host "  $installedVersion"
Write-Host ''
Write-Host 'Next step:'
Write-Host '  fastplace new my-app'
if ($pathModified) {
    Write-Host ''
    Write-Step 'PATH updated for your user — open a new terminal for it to take effect.'
}
Write-Host ''
Write-Step 'Docs: https://fastplace.dev — uninstall: uv tool uninstall fastplace'
