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

function Assert-Command {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Erforderliches Kommando nicht gefunden: $Name"
    }
}

function Invoke-Step {
    param(
        [string]$Title,
        [scriptblock]$Action
    )

    Write-Host "`n==> $Title" -ForegroundColor Cyan
    & $Action
}

Assert-Command git

if ($RemoteDeploy) {
    if (-not $RemoteHost -or -not $RemoteUser -or -not $RemotePath) {
        throw "Für -RemoteDeploy sind -RemoteHost, -RemoteUser und -RemotePath erforderlich."
    }

    Assert-Command ssh

    Invoke-Step "Git Branch prüfen" {
        git rev-parse --abbrev-ref HEAD
    }

    if (-not $SkipGitPull) {
        Invoke-Step "Lokales Git Pull (fast-forward only)" {
            git pull --ff-only
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
        & ssh @sshArgs
    }

    Write-Host "`nRemote-Deploy abgeschlossen." -ForegroundColor Green
    return
}

Assert-Command docker

Invoke-Step "Git Branch prüfen" {
    git rev-parse --abbrev-ref HEAD
}

if (-not $SkipGitPull) {
    Invoke-Step "Git Pull (fast-forward only)" {
        git pull --ff-only
    }
}

Invoke-Step "Docker Compose Pull" {
    docker compose pull
}

if (-not $SkipBuild) {
    Invoke-Step "Docker Compose Build" {
        docker compose build
    }
}

Invoke-Step "Docker Compose Up" {
    docker compose up -d
}

Invoke-Step "Container Status" {
    docker compose ps
}

Write-Host "`nDeploy abgeschlossen." -ForegroundColor Green
