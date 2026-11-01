param([switch]$OwnerOperate, [switch]$SyntheticCheck, [switch]$LauncherCheck, [switch]$ConnectionCheck)
Set-PSDebug -Off
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$taskProgram = Join-Path $PSScriptRoot 'owner_canary.py'
if (@($OwnerOperate, $SyntheticCheck, $LauncherCheck, $ConnectionCheck).Where({ $_ }).Count -gt 1) {
    Write-Host 'Stopped safely: code=MODE_CONFLICT; stage=mode-selection. No login or submit performed.'
    exit 2
}
if ($SyntheticCheck) {
    $taskTests = Join-Path $PSScriptRoot 'tests/test_owner_canary.py'
    if (-not (Test-Path -LiteralPath $taskTests)) { $taskTests = Join-Path $PSScriptRoot 'test_owner_canary.py' }
    & python -X utf8 -B $taskTests
    exit $LASTEXITCODE
}
if ($ConnectionCheck) {
    & python -X utf8 -B $taskProgram --connection-check
    exit $LASTEXITCODE
}
if (-not $OwnerOperate -and -not $LauncherCheck) {
    & python -X utf8 -B $taskProgram
    Write-Host 'Offline preview only. Owner live operation requires a separate interactive console and -OwnerOperate.'
    exit $LASTEXITCODE
}
if ($OwnerOperate -and ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected)) {
    Write-Host 'Owner operation requires an interactive console; no login or submit performed.'
    exit 2
}
$taskProcess = $null
$taskPointer = [IntPtr]::Zero
$taskSecure = $null
$taskPlain = $null
$taskStage = 'resolve-python'
$taskFailureCode = 'PYTHON_NOT_FOUND'
$taskOwnerStep = 'none'
try {
    # Match normal command resolution; never concatenate multiple executable paths.
    $taskPythonCommand = Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $taskPythonPath = $taskPythonCommand.Source
    $taskFailureCode = 'PYTHON_PATH_INVALID'
    if (-not $taskPythonPath -or -not (Test-Path -LiteralPath $taskPythonPath -PathType Leaf)) { throw 'Invalid executable' }
    $taskStage = 'process-setup'
    $taskFailureCode = 'PROCESS_SETUP_FAILED'
    $taskStart = [Diagnostics.ProcessStartInfo]::new()
    $taskStart.FileName = $taskPythonPath
    $taskArguments = @('-X','utf8','-B','-u',$taskProgram,'--owner-pipe')
    if ($LauncherCheck) {
        # Fixed synthetic program: no owner core, state, DB, authentication or sockets.
        $taskArguments = @('-X','utf8','-B','-u','-c','import json; print(json.dumps({"kind":"launch-check","synthetic":True}), flush=True)')
    }
    foreach ($taskArg in $taskArguments) {
        $taskStart.ArgumentList.Add($taskArg)
    }
    $taskStart.UseShellExecute = $false
    $taskStart.CreateNoWindow = $true
    $taskStart.RedirectStandardInput = $true
    $taskStart.RedirectStandardOutput = $true
    $taskStart.RedirectStandardError = $true
    $taskStart.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $taskStart.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
    # Never inherit ambient auth/config overrides; the core uses a fixed local origin.
    $taskStart.Environment.Clear()
    foreach ($taskName in @('SystemRoot','WINDIR','PATH','TEMP','TMP')) {
        $taskValue = [Environment]::GetEnvironmentVariable($taskName,'Process')
        if ($null -ne $taskValue) { $taskStart.Environment[$taskName] = $taskValue }
    }
    $taskStage = 'process-start'
    $taskFailureCode = 'PROCESS_START_FAILED'
    $taskProcess = [Diagnostics.Process]::Start($taskStart)
    $taskErrors = $taskProcess.StandardError.ReadToEndAsync()
    $taskStage = 'protocol-read'
    $taskFailureCode = 'PROTOCOL_READ_FAILED'
    $taskEventCount = 0
    while ($null -ne ($taskLine = $taskProcess.StandardOutput.ReadLine())) {
        $taskStage = 'protocol-parse'
        $taskFailureCode = 'PROTOCOL_INVALID'
        $taskEvent = $taskLine | ConvertFrom-Json -AsHashtable
        $taskEventCount++
        if ($LauncherCheck -and $taskEvent.kind -ne 'launch-check') { throw 'Invalid synthetic protocol' }
        switch ($taskEvent.kind) {
            'launch-check' {
                if (-not $LauncherCheck -or $taskEvent.synthetic -ne $true) { throw 'Invalid synthetic protocol' }
                @{ ok=$true; mode='synthetic-launch-check'; realAuthenticationExecuted=$false; realApiWritesExecuted=$false; realStateRead=$false } | ConvertTo-Json -Compress
            }
            'confirm' {
                $taskStep = $taskEvent.step
                $taskChannel = $taskEvent.channel
                $taskLabels = @{
                    'login'='Authenticate the original Owner by hidden email/password, tenant1 only'
                    'create-kb'='Create the named empty keyword-only canary KB'
                    'recover-kb'='Adopt the matching KB from an earlier uncertain submission; no POST retry'
                    'create-and-save-writer'='Create AND CurrentUser-DPAPI-save one single-KB ingest+retrieve writer, expiry final submission +7 days'
                    'resume-protected-writer'='Read this canary writer protected file locally and verify exact identity; no new key'
                    'save-recovered-writer'='Save the Owner hidden-input original writer with CurrentUser DPAPI; no replacement key'
                }
                if (-not $taskLabels.ContainsKey($taskStep)) { throw 'Unsupported prompt' }
                $taskOwnerStep = $taskStep
                Write-Host $taskLabels[$taskStep]
                if ($taskChannel) { Write-Host ('Channel: '+$taskChannel) }
                if ($taskEvent.expiresUnix) {
                    Write-Host ('Expiry UTC (recalculated immediately before submit): '+[DateTimeOffset]::FromUnixTimeSeconds($taskEvent.expiresUnix).ToString('o'))
                }
                $taskStage = 'owner-input'
                $taskFailureCode = 'OWNER_INPUT_FAILED'
                $taskAnswer = Read-Host 'Type CONFIRM to proceed; anything else cancels'
                $taskStage = 'owner-input-send'
                $taskFailureCode = 'OWNER_INPUT_SEND_FAILED'
                $taskProcess.StandardInput.WriteLine((@{value=($taskAnswer -ceq 'CONFIRM')} | ConvertTo-Json -Compress))
            }
            'secret' {
                if ($taskEvent.step -notin @('email','password','recover-token')) { throw 'Unsupported hidden prompt' }
                $taskOwnerStep = $taskEvent.step
                $taskStage = 'owner-input'
                $taskFailureCode = 'OWNER_INPUT_FAILED'
                $taskSecure = Read-Host ('Owner local hidden input: '+$taskEvent.step) -AsSecureString
                $taskPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($taskSecure)
                $taskPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($taskPointer)
                $taskStage = 'owner-input-send'
                $taskFailureCode = 'OWNER_INPUT_SEND_FAILED'
                $taskProcess.StandardInput.WriteLine((@{value=$taskPlain} | ConvertTo-Json -Compress))
                $taskPlain = $null
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskPointer)
                $taskPointer = [IntPtr]::Zero
                $taskSecure.Dispose()
                $taskSecure = $null
            }
            'receipt' { $taskEvent.data | ConvertTo-Json -Depth 6 }
            'blocked' {
                $taskDiagnostic = @{code=$taskEvent.code; ownerStep=$taskOwnerStep}
                if ($taskEvent.stage -in @('service-health','auth-login','auth-identity','kb-create','writer-create','kb-detail')) { $taskDiagnostic.stage=$taskEvent.stage }
                if ($taskEvent.transport -in @('HTTP_STATUS','ACCESS_DENIED','CONNECTION_REFUSED','CONNECTION_RESET','TIMEOUT','INVALID_JSON','UNAVAILABLE')) { $taskDiagnostic.transport=$taskEvent.transport }
                if ($taskEvent.httpStatus -is [long] -or $taskEvent.httpStatus -is [int]) {
                    if ($taskEvent.httpStatus -ge 100 -and $taskEvent.httpStatus -le 599) { $taskDiagnostic.httpStatus=$taskEvent.httpStatus }
                }
                Write-Host 'Stopped safely. State retained; do not recreate or blindly retry.'
                $taskDiagnostic | ConvertTo-Json -Compress
            }
            default { throw 'Unsupported protocol' }
        }
        $taskStage = 'protocol-read'
        $taskFailureCode = 'PROTOCOL_READ_FAILED'
    }
    $taskStage = 'process-exit'
    $taskFailureCode = 'CHILD_EXIT_CHECK_FAILED'
    $taskProcess.WaitForExit()
    $null = $taskErrors.GetAwaiter().GetResult()
    if ($taskEventCount -eq 0) {
        $taskFailureCode = 'CHILD_NO_PROTOCOL'
        throw 'No protocol'
    }
    exit $taskProcess.ExitCode
} catch {
    Write-Host ('Owner entry stopped safely. code='+$taskFailureCode+'; stage='+$taskStage+'; ownerStep='+$taskOwnerStep+'. State retained; no automatic retry. No raw exception or process response is displayed.')
    exit 2
} finally {
    try { if ($taskPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($taskPointer) } } catch { }
    try { if ($taskSecure) { $taskSecure.Dispose() } } catch { }
    $taskPlain = $null
    if ($taskProcess) {
        try { $taskProcess.StandardInput.Close() } catch { }
        try { if (-not $taskProcess.HasExited -and -not $taskProcess.WaitForExit(3000)) { $taskProcess.Kill() } } catch { }
        try { $taskProcess.Dispose() } catch { }
    }
}
