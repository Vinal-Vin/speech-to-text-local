<#
.SYNOPSIS
    Set up the stt-local Windows client. No administrator rights required.

.DESCRIPTION
    Creates a virtual environment and installs the client dependencies.

    If Python is not installed and cannot be installed (a real possibility on
    a managed machine), pass -Embeddable. That downloads the official Python
    embeddable ZIP into this folder and uses it: no installer, no admin, no
    registry writes, nothing outside your user directory.

    -AddToStartup creates a shortcut in your per-user Startup folder so the
    client launches at sign-in. This writes only to your own profile: no admin
    rights, no registry Run key, no machine-wide changes. Undo with
    -RemoveFromStartup.

.EXAMPLE
    .\scripts\setup-client.ps1
    .\scripts\setup-client.ps1 -Embeddable
    .\scripts\setup-client.ps1 -AddToStartup
    .\scripts\setup-client.ps1 -RemoveFromStartup
#>
param(
    [switch]$Embeddable,
    [switch]$AddToStartup,
    [switch]$RemoveFromStartup,
    [string]$PythonVersion = "3.12.8"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "stt-local client setup" -ForegroundColor Cyan
Write-Host "======================`n"

$StartupLink = Join-Path ([Environment]::GetFolderPath('Startup')) "stt-local.lnk"

# Removal is handled before anything else so it works even if the environment
# was never fully set up.
if ($RemoveFromStartup) {
    if (Test-Path $StartupLink) {
        Remove-Item $StartupLink -Force
        Write-Host "Removed from Startup: $StartupLink" -ForegroundColor Green
    }
    else {
        Write-Host "Nothing to remove -- no Startup entry found."
    }
    exit 0
}

function Test-Python {
    try {
        $v = & python --version 2>&1
        if ($LASTEXITCODE -eq 0) { return $v.ToString() }
    } catch { }
    return $null
}

if (-not $Embeddable) {
    $ver = Test-Python
    if ($null -eq $ver) {
        Write-Host "Python not found on PATH." -ForegroundColor Yellow
        Write-Host "If you cannot install Python, re-run with:" -ForegroundColor Yellow
        Write-Host "    .\scripts\setup-client.ps1 -Embeddable`n" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Found $ver"

    $venv = Join-Path $Root ".venv"
    if (-not (Test-Path $venv)) {
        Write-Host "Creating virtual environment..."
        & python -m venv $venv
    }
    $py = Join-Path $venv "Scripts\python.exe"
}
else {
    # --- No-admin path: the official embeddable distribution ---
    $embedDir = Join-Path $Root ".python-embed"
    $py = Join-Path $embedDir "python.exe"

    if (-not (Test-Path $py)) {
        $zipUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
        $zip = Join-Path $env:TEMP "python-embed-$PythonVersion.zip"
        Write-Host "Downloading embeddable Python $PythonVersion..."
        Invoke-WebRequest -Uri $zipUrl -OutFile $zip -UseBasicParsing
        Write-Host "Extracting to $embedDir"
        Expand-Archive -Path $zip -DestinationPath $embedDir -Force
        Remove-Item $zip -Force

        # The embeddable build ships with site-packages disabled via a
        # ._pth file; enabling `import site` is what lets pip work at all.
        Get-ChildItem $embedDir -Filter "python*._pth" | ForEach-Object {
            (Get-Content $_.FullName) -replace '^#\s*import site', 'import site' |
                Set-Content $_.FullName
        }

        Write-Host "Bootstrapping pip..."
        $getPip = Join-Path $embedDir "get-pip.py"
        Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
        & $py $getPip --no-warn-script-location
        Remove-Item $getPip -Force
    }
    Write-Host "Using embeddable Python at $embedDir"
}

Write-Host "`nInstalling client dependencies..."
& $py -m pip install --upgrade pip --quiet --no-warn-script-location
& $py -m pip install -r (Join-Path $Root "client\requirements.txt") --no-warn-script-location
if ($LASTEXITCODE -ne 0) { Write-Host "Dependency install failed." -ForegroundColor Red; exit 1 }

# Seed the user config on first run.
$cfgDir = Join-Path $env:APPDATA "stt-local"
$cfg = Join-Path $cfgDir "config.toml"
if (-not (Test-Path $cfg)) {
    New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
    Copy-Item (Join-Path $Root "client\config.example.toml") $cfg
    Write-Host "`nCreated config: $cfg" -ForegroundColor Green
}
else {
    Write-Host "`nConfig already exists: $cfg"
}

# --- Optional: launch at sign-in -------------------------------------------
if ($AddToStartup) {
    # pythonw.exe runs without a console window -- this is a tray app, so a
    # stray black window at every sign-in would be a nuisance.
    $pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
    if (-not (Test-Path $pyw)) { $pyw = $py }

    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($StartupLink)
    $lnk.TargetPath = $pyw
    $lnk.Arguments = "-m stt_client"
    $lnk.WorkingDirectory = Join-Path $Root "client"
    $lnk.Description = "stt-local push-to-talk dictation"
    $lnk.WindowStyle = 7   # minimised
    $lnk.Save()

    Write-Host "`nAdded to Startup: $StartupLink" -ForegroundColor Green
    Write-Host "  target: $pyw -m stt_client"
    Write-Host "  Remove with: .\scripts\setup-client.ps1 -RemoveFromStartup"
    Write-Host "  Note: the Docker server must also be running for it to work;" -ForegroundColor Yellow
    Write-Host "        'restart: unless-stopped' in compose handles that."   -ForegroundColor Yellow
}
elseif (-not (Test-Path $StartupLink)) {
    Write-Host "`nTip: run with -AddToStartup to launch the client at sign-in."
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host @"

Next steps:
  1. Check this machine can run the client:
       $py scripts\doctor.py

  2. Start the ASR server (first run downloads ~600 MB):
       docker compose up -d
       docker compose logs -f          # wait for "backend ready"

  3. Test transcription without insertion:
       cd client; $py -m stt_client --print-only

  4. Run for real:
       cd client; $py -m stt_client
"@
