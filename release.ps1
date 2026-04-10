param(
    [Parameter(Mandatory = $true)]
    [string]$CommitMessage,
    [switch]$SkipDeploy,
    [switch]$SkipBuild,
    [switch]$AllowEmptyCommit,
    [string]$RemoteHost = "v2202604349663448661.powersrv.de",
    [string]$RemoteUser = "root",
    [string]$RemotePath = "/opt/fahrmanager",
    [string]$SshKeyPath = "$env:USERPROFILE\.ssh\id_ed25519_deploy",
    [int]$SshPort = 22
)

$ErrorActionPreference = "Stop"

function Resolve-CommandPath {
    param(
        [string]$Name,
        [string[]]$FallbackPaths = @()
    )

    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        return $cmd.Source
    }

    foreach ($path in $FallbackPaths) {
        if ($path -and (Test-Path $path)) {
            return $path
        }
    }

    throw "Required command not found: $Name"
}

function Invoke-Step {
    param(
        [string]$Title,
        [scriptblock]$Action
    )

    Write-Host "`n==> $Title" -ForegroundColor Cyan
    & $Action
}

$gitExe = Resolve-CommandPath -Name "git" -FallbackPaths @(
    "C:\Program Files\Git\cmd\git.exe",
    "C:\Program Files\Git\bin\git.exe",
    "C:\Program Files (x86)\Git\cmd\git.exe",
    "C:\Program Files (x86)\Git\bin\git.exe"
)

Invoke-Step "Git Add (all changes)" {
    & $gitExe add -A
}

Invoke-Step "Git Status (staged)" {
    & $gitExe diff --cached --name-status
}

& $gitExe diff --cached --quiet
$hasStagedChanges = $LASTEXITCODE -ne 0

if (-not $hasStagedChanges -and -not $AllowEmptyCommit) {
    throw "No staged changes found. Nothing to commit."
}

Invoke-Step "Git Commit" {
    if ($hasStagedChanges) {
        & $gitExe commit -m $CommitMessage
    }
    else {
        & $gitExe commit --allow-empty -m $CommitMessage
    }
}

Invoke-Step "Git Push" {
    & $gitExe push origin main
}

if ($SkipDeploy) {
    Write-Host "`nRelease completed (deploy skipped)." -ForegroundColor Yellow
    return
}

$deployScript = Join-Path $PSScriptRoot "deploy.ps1"
if (-not (Test-Path $deployScript)) {
    throw "deploy.ps1 not found: $deployScript"
}

$deployArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $deployScript,
    "-RemoteDeploy",
    "-RemoteHost", $RemoteHost,
    "-RemoteUser", $RemoteUser,
    "-RemotePath", $RemotePath,
    "-SshPort", $SshPort.ToString(),
    "-SshKeyPath", $SshKeyPath
)

if ($SkipBuild) {
    $deployArgs += "-SkipBuild"
}

Invoke-Step "Remote Deploy" {
    & powershell @deployArgs
}

Write-Host "`nRelease + deploy completed." -ForegroundColor Green
