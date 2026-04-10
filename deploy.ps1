param(
    [switch]$SkipGitPull,
    [switch]$SkipBuild,
    [switch]$RemoteDeploy,
    [string]$RemoteHost,
    [string]$RemoteUser,
    [string]$RemotePath,
    [string]$SshKeyPath,
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

    throw "Erforderliches Kommando nicht gefunden: $Name"
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

if ($RemoteDeploy) {
    if (-not $RemoteHost -or -not $RemoteUser -or -not $RemotePath) {
        throw "Für -RemoteDeploy sind -RemoteHost, -RemoteUser und -RemotePath erforderlich."
    }

    $sshExe = Resolve-CommandPath -Name "ssh" -FallbackPaths @(
        "C:\Windows\System32\OpenSSH\ssh.exe"
    )

    Invoke-Step "Git Branch prüfen" {
        & $gitExe rev-parse --abbrev-ref HEAD
    }

    if (-not $SkipGitPull) {
        Invoke-Step "Lokales Git Pull (fast-forward only)" {
            & $gitExe pull --ff-only
        }
    }

    $remoteCommands = @(
        "set -e",
        "cd '$RemotePath'",
        "git pull --ff-only",
        "docker compose pull"
    )

    if (-not $SkipBuild) {
        $remoteCommands += "docker compose build"
    }

    $remoteCommands += @(
        "docker compose up -d",
        "docker compose ps"
    )

    $remoteCommandString = [string]::Join(" && ", $remoteCommands)

    $sshArgs = @("-p", $SshPort.ToString())
    if ($SshKeyPath) {
        $sshArgs += @("-i", $SshKeyPath)
    }
    $sshArgs += @("$RemoteUser@$RemoteHost", $remoteCommandString)

    Invoke-Step "Remote Deploy auf $RemoteHost" {
        & $sshExe @sshArgs
    }

    Write-Host "`nRemote-Deploy abgeschlossen." -ForegroundColor Green
    return
}

$dockerExe = Resolve-CommandPath -Name "docker" -FallbackPaths @(
    "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
)

Invoke-Step "Git Branch prüfen" {
    & $gitExe rev-parse --abbrev-ref HEAD
}

if (-not $SkipGitPull) {
    Invoke-Step "Git Pull (fast-forward only)" {
        & $gitExe pull --ff-only
    }
}

Invoke-Step "Docker Compose Pull" {
    & $dockerExe compose pull
}

if (-not $SkipBuild) {
    Invoke-Step "Docker Compose Build" {
        & $dockerExe compose build
    }
}

Invoke-Step "Docker Compose Up" {
    & $dockerExe compose up -d
}

Invoke-Step "Container Status" {
    & $dockerExe compose ps
}

Write-Host "`nDeploy abgeschlossen." -ForegroundColor Green
