# UniEnv AI bootstrap for Windows (PowerShell).
#
#   irm https://raw.githubusercontent.com/rupeshbharambe24/unienv-ai/main/install.ps1 | iex
#   .\install.ps1                     install default batteries and wire everything
#   .\install.ps1 --dev               copy this checkout (no administrator privileges needed)
#   .\install.ps1 --no-system         skip winget/Scoop packages
#   .\install.ps1 --batteries office,pdf
#
# Everything after the bootstrap flags is passed to `unienv install`.

$ErrorActionPreference = "Stop"

$Repo = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$Ref = if ($env:UNIENV_REF) { $env:UNIENV_REF } else { "main" }
$UniEnvHome = if ($env:UNIENV_HOME) { $env:UNIENV_HOME } else { Join-Path $HOME ".unienv" }
$env:UNIENV_HOME = $UniEnvHome
$env:PYTHONUTF8 = "1"

$Dev = $false
$ForwardArgs = @()
foreach ($a in $args) {
    switch ($a) {
        "--dev" { $Dev = $true }
        { $_ -in "-h", "--help" } {
            Write-Host "Usage: .\install.ps1 [--dev] [--no-system] [--batteries <list>]"
            exit 0
        }
        default { $ForwardArgs += $a }
    }
}

function Say($msg) {
    Write-Host "==> " -ForegroundColor Cyan -NoNewline
    Write-Host $msg
}

# 0. Download tarball if not run from a local checkout
$TmpSrc = $null
try {
    if (-not (Test-Path (Join-Path $Repo "src\unienv.py"))) {
        if ($Dev) {
            Write-Error "--dev needs a git checkout: git clone https://github.com/rupeshbharambe24/unienv-ai"
            exit 2
        }
        Say "Downloading UniEnv AI ($Ref)"
        $TmpSrc = Join-Path ([System.IO.Path]::GetTempPath()) ("unienv-" + [System.Guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Force -Path $TmpSrc | Out-Null
        $ZipPath = Join-Path $TmpSrc "unienv.zip"
        Invoke-WebRequest -Uri "https://github.com/rupeshbharambe24/unienv-ai/archive/$Ref.zip" -OutFile $ZipPath
        Expand-Archive -Path $ZipPath -DestinationPath $TmpSrc -Force
        $Extracted = Get-ChildItem -Directory -Path $TmpSrc -Filter "unienv-ai-*" | Select-Object -First 1
        $Repo = $Extracted.FullName
    }

    # 1. Locate or install uv
    $UvCmd = Get-Command uv -ErrorAction SilentlyContinue
    $Uv = if ($UvCmd) { $UvCmd.Source } else { $null }
    if (-not $Uv) {
        foreach ($c in @(
            (Join-Path $HOME ".local\bin\uv.exe"),
            (Join-Path $HOME ".cargo\bin\uv.exe"),
            (Join-Path $HOME "scoop\shims\uv.exe")
        )) {
            if (Test-Path $c) { $Uv = $c; break }
        }
    }
    if (-not $Uv) {
        Say "Installing uv (https://docs.astral.sh/uv/)"
        irm https://astral.sh/uv/install.ps1 | iex
        $Uv = Join-Path $HOME ".local\bin\uv.exe"
    }

    # 2. Install uv-managed Python 3.12
    Say "Python 3.12 (uv-managed)"
    & $Uv python install 3.12 --quiet
    if ($LASTEXITCODE -ne 0) { throw "uv python install failed" }
    $Py = (& $Uv python find 3.12 --managed-python).Trim()
    if ($LASTEXITCODE -ne 0) { throw "uv python find failed" }

    # 3. Populate share directory
    New-Item -ItemType Directory -Force -Path $UniEnvHome | Out-Null
    $ShareDir = Join-Path $UniEnvHome "share"
    # Never recurse through an old development link or a nested junction.
    if (Test-Path -LiteralPath $ShareDir) {
        $Existing = Get-Item -LiteralPath $ShareDir -Force
        if ($Existing.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "share is a link; remove the link manually before reinstalling. Its target was not changed."
        }
        $Links = Get-ChildItem -LiteralPath $ShareDir -Recurse -Force |
            Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }
        if ($Links) { throw "share contains links; remove them manually before reinstalling." }
        if ([IO.Path]::GetFullPath($ShareDir) -eq [IO.Path]::GetFullPath($Repo)) {
            throw "Cannot replace the source checkout."
        }
        Remove-Item -LiteralPath $ShareDir -Recurse -Force
    }
    # Copies work on stock Windows without Developer Mode or symlink privileges.
    New-Item -ItemType Directory -Force -Path $ShareDir | Out-Null
    foreach ($part in @("src", "recipes", "templates", "shims")) {
        Copy-Item -Recurse -Force (Join-Path $Repo $part) (Join-Path $ShareDir $part)
    }

    # 4. Ensure ~/.unienv/bin is on the user PATH
    $BinDir = Join-Path $UniEnvHome "bin"
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $UserPath) { $UserPath = "" }
    if (($UserPath -split ";") -notcontains $BinDir) {
        $NewUserPath = if ($UserPath) { "$BinDir;$UserPath" } else { $BinDir }
        @{ entry = $BinDir } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $UniEnvHome "windows-path.json")
        [Environment]::SetEnvironmentVariable("Path", $NewUserPath, "User")
    }
    $env:Path = "$(Split-Path $Uv -Parent);$BinDir;$env:Path"

    # 5. Hand over to the CLI
    & $Py (Join-Path $ShareDir "src\unienv.py") install @ForwardArgs
    if ($LASTEXITCODE -ne 0) { throw "UniEnv install failed ($LASTEXITCODE)" }
} finally {
    if ($TmpSrc -and (Test-Path $TmpSrc)) {
        Remove-Item -Recurse -Force $TmpSrc -ErrorAction SilentlyContinue
    }
}
