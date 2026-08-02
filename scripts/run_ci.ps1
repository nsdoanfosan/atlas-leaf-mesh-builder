[CmdletBinding()]
param(
    [switch]$InstallDependencies
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path -Path $PSScriptRoot -ChildPath '..'))
Set-Location -LiteralPath $repoRoot

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    Write-Host "==> $Description"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Get-RepositoryState {
    $state = @(git status --porcelain=v1 --untracked-files=all --ignored=matching)
    if ($LASTEXITCODE -ne 0) {
        throw "git status failed with exit code $LASTEXITCODE"
    }
    return $state
}

$initialState = @(Get-RepositoryState)
if ($initialState.Count -ne 0) {
    $initialState | Write-Host
    throw 'CI must start from a clean repository, including ignored generated outputs.'
}

if ($InstallDependencies) {
    Invoke-NativeCommand 'Install hash-locked CI dependencies' {
        python -m pip install --disable-pip-version-check --require-hashes --requirement requirements-ci.txt
    }
}

$previousDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
$previousPycachePrefix = $env:PYTHONPYCACHEPREFIX
$bytecodeRoot = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath (
    'atlas-producer-ci-' + [System.Guid]::NewGuid().ToString('N')
)
[System.IO.Directory]::CreateDirectory($bytecodeRoot) | Out-Null
$pythonSourceRoots = @('addons', 'scripts', 'tests')
if (Test-Path -LiteralPath 'tools' -PathType Container) {
    $pythonSourceRoots += 'tools'
}

try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Invoke-NativeCommand 'Run the complete producer test suite' {
        python -m pytest -q -p no:cacheprovider
    }

    $env:PYTHONPYCACHEPREFIX = $bytecodeRoot
    Invoke-NativeCommand 'Compile every Python source file' {
        python -m compileall -q -f $pythonSourceRoots
    }
}
finally {
    $env:PYTHONDONTWRITEBYTECODE = $previousDontWriteBytecode
    $env:PYTHONPYCACHEPREFIX = $previousPycachePrefix
    if ([System.IO.Directory]::Exists($bytecodeRoot)) {
        [System.IO.Directory]::Delete($bytecodeRoot, $true)
    }
}

Invoke-NativeCommand 'Check working-tree whitespace' {
    git diff --check
}

$diffBase = $env:CI_DIFF_BASE
$hasDiffBase = -not [string]::IsNullOrWhiteSpace($diffBase) -and $diffBase -notmatch '^0+$'
if ($hasDiffBase) {
    git cat-file -e "$diffBase`^{commit}"
    if ($LASTEXITCODE -ne 0) {
        throw "CI diff base is unavailable: $diffBase"
    }
}
else {
    git rev-parse --verify 'HEAD^' *> $null
    if ($LASTEXITCODE -eq 0) {
        $diffBase = 'HEAD^'
        $hasDiffBase = $true
    }
}

if ($hasDiffBase) {
    Invoke-NativeCommand "Check committed whitespace since $diffBase" {
        git diff --check "$diffBase..HEAD"
    }
}

$finalState = @(Get-RepositoryState)
if ($finalState.Count -ne 0) {
    $finalState | Write-Host
    throw 'Tests or static checks left tracked, untracked, or ignored repository artifacts.'
}

Write-Host 'Producer CI passed with a clean repository.'
