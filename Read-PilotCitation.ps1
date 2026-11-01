# Unreleased task helper: reuse the existing protected reader, GET one known citation.
param([switch]$UseProtectedRecovery)
Set-PSDebug -Off
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
if (-not $UseProtectedRecovery) { throw 'EXISTING_PROTECTED_READER_REQUIRED_NO_SECRET_PROMPT' }
$cliRoot = 'D:/services/weknora/cli'
$python = 'C:/Users/33272/miniconda3/python.exe'
$cli = Join-Path $cliRoot 'weknora-cli.exe'
$docId = '0390fad7-593f-4323-90f6-d4bd5426a44f'
$chunkId = '158e5cda-4450-4619-a755-54a01f7675e0'
$kbId = 'da1051a5-3fff-4198-ba55-017ea79df9c3'

function Invoke-ReadTool([string]$File, [string[]]$Arguments, [string]$InputJson = '') {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $File
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if ($InputJson) { $process.StandardInput.Write($InputJson) }
        $process.StandardInput.Close()
        if (-not $process.WaitForExit(30000)) {
            $process.Kill($true)
            throw 'READ_COMMAND_TIMEOUT_OUTCOME_UNKNOWN_NO_RETRY'
        }
        $text = $stdout.GetAwaiter().GetResult()
        $null = $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) { throw ('READ_COMMAND_REJECTED_EXIT_' + $process.ExitCode + '_NO_RETRY') }
        if ($text.Length -gt 1048576) { throw 'READ_RESPONSE_TOO_LARGE' }
        return $text
    } finally { $process.Dispose() }
}

$pins = @{
    'Enter-PilotCli.ps1' = 'b05c380ab574d1fb6082a57174851c9985e232c72d3e926e1f6cc0b4a99f1480'
    'ReaderMetadataGuard.py' = '0bc2487f330ac722184940f0140aa1bf9fae6ac338f6d987f1dee00aafd8f137'
    'ReaderBoundaryGuard.py' = '7af00c27a8133c25b8aa03ac8b9ef545d6745ff07f5feaa19ee1008fd947d132'
    'weknora-cli.exe' = '2c95e68e92a6de647e3c5ab82b767f8725e06934fe3a143c74123f578b74bec8'
}
foreach ($name in $pins.Keys) {
    if ((Get-FileHash -LiteralPath (Join-Path $cliRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pins[$name]) { throw 'EXISTING_READER_ARTIFACT_CHANGED' }
}
$guard = Join-Path $cliRoot 'ReaderMetadataGuard.py'
$pre = (Invoke-ReadTool $python @('-X','utf8','-B',$guard,'--metadata-only')) | ConvertFrom-Json
if (-not $pre.ok -or -not $pre.data.metadataApproved) { throw 'EXISTING_READER_METADATA_REJECTED' }
foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -match '^WEKNORA_[A-Z0-9_]*$' })) { [Environment]::SetEnvironmentVariable($item.Name,$null,'Process') }
$env:XDG_CONFIG_HOME = Join-Path $cliRoot 'config'
$env:XDG_CACHE_HOME = Join-Path $cliRoot 'cache'
$env:XDG_DATA_HOME = Join-Path $cliRoot 'data'
$env:WEKNORA_HOST = 'http://127.0.0.1:8081'
$env:WEKNORA_PROFILE = 'shidai3-pilot'
$env:WEKNORA_LOG_LEVEL = 'error'
$secure = $null
$pointer = [IntPtr]::Zero
$stage = 'protected_reader_loading'
$completedReads = @()
try {
    # Same process-local protected-reader loading as the existing normal entry.
    # Never returns, logs, copies or persists the secret.
    $secure = ConvertTo-SecureString (Get-Content -LiteralPath (Join-Path $cliRoot 'private/pilot-recovery-reader.dpapi') -Raw).Trim()
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    [Environment]::SetEnvironmentVariable('WEKNORA_API_KEY',[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer),'Process')
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $pointer = [IntPtr]::Zero
    $stage = 'reader_binding_verification'
    $bound = (Invoke-ReadTool $python @('-X','utf8','-B',$guard,'--verify-authenticated')) | ConvertFrom-Json
    if (-not $bound.ok -or -not $bound.data.credentialBindingVerified -or $bound.data.tenantId -ne 1 -or $bound.data.keyId -ne 3 -or $bound.data.kbId -ne $kbId) { throw 'EXISTING_READER_BINDING_REJECTED' }
    $stage = 'reader_boundary_verification'
    $boundary = Invoke-ReadTool $python @('-X','utf8','-B',(Join-Path $cliRoot 'ReaderBoundaryGuard.py'))
    if ($boundary.Trim() -cne 'Read-only key-management and foreign-KB rejection checks passed.') { throw 'EXISTING_READER_BOUNDARY_OUTPUT_REJECTED' }
    $stage = 'known_document_get'
    $document = (Invoke-ReadTool $cli @('doc','view',$docId,'--format','json')) | ConvertFrom-Json
    $completedReads += 'known_document_get'
    if (-not $document.ok -or $document.data.id -ne $docId -or $document.data.knowledge_base_id -ne $kbId -or $document.data.title -ne 'README-P1060.md') { throw 'DOCUMENT_SCOPE_REJECTED_NO_CHUNK_READ' }
    $stage = 'known_chunk_get'
    $chunk = (Invoke-ReadTool $cli @('chunk','view',$chunkId,'--format','json')) | ConvertFrom-Json
    $completedReads += 'known_chunk_get'
    [Environment]::SetEnvironmentVariable('WEKNORA_API_KEY',$null,'Process')
    $payload = @{ document=$document; chunk=$chunk; binding=$bound.data } | ConvertTo-Json -Depth 30 -Compress
    $stage = 'citation_validation_and_local_comparison'
    $result = Invoke-ReadTool $python @('-X','utf8','-B',(Join-Path $PSScriptRoot 'reader_citations.py')) $payload
    Write-Output $result
} catch {
    # Never print native stderr, authentication bodies, secret values or input data.
    @{ok=$false;error='PILOT_CITATION_READ_STOPPED_NO_RETRY';stage=$stage;completedTypedReads=$completedReads;automaticRetry=$false} | ConvertTo-Json -Compress
    exit 1
} finally {
    [Environment]::SetEnvironmentVariable('WEKNORA_API_KEY',$null,'Process')
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if ($secure) { $secure.Dispose() }
}
