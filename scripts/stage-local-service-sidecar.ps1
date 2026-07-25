param(
    [Parameter(Mandatory = $true)]
    [string]$Source,

    [Parameter(Mandatory = $true)]
    [string]$DestinationRoot,

    [Parameter(Mandatory = $true)]
    [string]$WorkspaceRoot
)

$ErrorActionPreference = 'Stop'
$executableName = 'daon-user-local-service-x86_64-pc-windows-msvc.exe'
$createdTarget = $false
$target = $null

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Value)
    return [System.IO.Path]::GetFullPath($Value).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash($stream)
        return -join ($bytes | ForEach-Object { $_.ToString('X2') })
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

try {
    $resolvedSource = (Resolve-Path -LiteralPath $Source).Path
    $resolvedWorkspace = (Resolve-Path -LiteralPath $WorkspaceRoot).Path
    $tempRoot = Get-NormalizedPath ([System.IO.Path]::GetTempPath())
    $sourcePath = Get-NormalizedPath $resolvedSource
    $workspacePath = Get-NormalizedPath $resolvedWorkspace
    $destinationPath = Get-NormalizedPath $DestinationRoot
    $expectedDestination = Get-NormalizedPath (
        [System.IO.Path]::Combine(
            $workspacePath,
            'apps',
            'desktop',
            'src-tauri',
            'binaries'
        )
    )

    $tempPrefix = $tempRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $sourcePath.StartsWith(
        $tempPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'Sidecar source must be under the isolated temporary root.'
    }
    if (-not $destinationPath.Equals(
        $expectedDestination,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'Sidecar destination is outside the exact workspace binaries directory.'
    }
    if (-not [System.IO.Path]::GetFileName($sourcePath).Equals(
        'daon-user-local-service.exe',
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'Unexpected sidecar source filename.'
    }

    [System.IO.Directory]::CreateDirectory($expectedDestination) | Out-Null
    $resolvedDestination = (Resolve-Path -LiteralPath $expectedDestination).Path
    if (-not (Get-NormalizedPath $resolvedDestination).Equals(
        $expectedDestination,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'Resolved destination does not match the validated destination.'
    }

    $target = [System.IO.Path]::Combine($expectedDestination, $executableName)
    if (Test-Path -LiteralPath $target) {
        throw 'Refusing to overwrite an existing generated sidecar.'
    }

    [System.IO.File]::Copy($sourcePath, $target, $false)
    $createdTarget = $true
    $sourceInfo = Get-Item -LiteralPath $sourcePath
    $targetInfo = Get-Item -LiteralPath $target
    $sourceHash = Get-Sha256 $sourcePath
    $targetHash = Get-Sha256 $target
    if ($sourceInfo.Length -ne $targetInfo.Length -or $sourceHash -ne $targetHash) {
        throw 'Sidecar staging integrity check failed.'
    }

    [ordered]@{
        schema_version = '1.0'
        target = $target
        bytes = $targetInfo.Length
        sha256 = $targetHash
        source_target_match = $true
    } | ConvertTo-Json -Compress
}
catch {
    if ($createdTarget -and $null -ne $target -and (Test-Path -LiteralPath $target)) {
        Remove-Item -LiteralPath $target -Force
    }
    throw
}
