param(
 [string]$CandidateSource='C:\Users\33272\AppData\Local\Temp\knowledge-sync-native-canary-20261009\source',
 [string]$OutputDirectory='C:\Users\33272\AppData\Local\Temp\knowledge-sync-native-canary-20261009\bin'
)
# Offline build only. Never starts WeKnora, accesses its DB, installs a tool or edits its original source.
$ErrorActionPreference='Stop'
$canarySource=[IO.Path]::GetFullPath($CandidateSource)
$canaryOutput=[IO.Path]::GetFullPath($OutputDirectory)
if ($canarySource -eq 'D:\services\weknora\build\upstream-v0.8.2' -or
    $canaryOutput.StartsWith('D:\services\weknora\',[StringComparison]::OrdinalIgnoreCase)) {
 throw 'Isolated source and output required'
}
if (-not (Test-Path -LiteralPath (Join-Path $canarySource 'internal/synccanary/store.go'))) {throw 'Candidate overlay absent'}
$canaryTools='D:\services\weknora\tools'
foreach ($canaryName in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
 if ($canaryName -match '^(GO|CGO_)' -or $canaryName -in @('CC','CXX','PKG_CONFIG')) {
  [Environment]::SetEnvironmentVariable($canaryName,$null,'Process')
 }
}
$env:GOENV='off'
$env:GOOS='windows'
$env:GOARCH='amd64'
$env:GOAMD64='v1'
$env:GOFLAGS='-mod=readonly'
$env:GOPROXY='off'
$env:GOSUMDB='off'
$env:GOTOOLCHAIN='local'
$env:GOTELEMETRY='off'
$env:CGO_ENABLED='1'
$canaryGo=Join-Path $canaryTools 'go/bin/go.exe'
if ((& $canaryGo version) -ne 'go version go1.26.8 windows/amd64') {throw 'Pinned Go mismatch'}
$env:CC=Join-Path $canaryTools 'msys64/ucrt64/bin/gcc.exe'
$env:CXX=Join-Path $canaryTools 'msys64/ucrt64/bin/g++.exe'
if ((& $env:CC -dumpfullversion -dumpversion).Trim() -ne '16.2.0') {throw 'Pinned GCC mismatch'}
if ((Get-FileHash -LiteralPath 'D:\services\weknora\downloads\libduckdb-windows-amd64-v1.5.2.zip').Hash.ToLowerInvariant() -ne 'c60bd7deb0ef6c2d5c12a9765b93ed930c34b984d4db79104a8c2955bc57017d') {throw 'Pinned DuckDB archive mismatch'}
$env:PATH=(Join-Path $canaryTools 'go/bin')+';'+(Join-Path $canaryTools 'msys64/ucrt64/bin')+';'+(Join-Path $canaryTools 'duckdb-v1.5.2')+';'+$env:PATH
$env:GOCACHE=Join-Path $canarySource 'internal/synccanary/.buildcache'
$env:GOMODCACHE='D:\services\weknora\build\go-modcache'
$env:GOTMPDIR=Join-Path (Split-Path $canarySource -Parent) 'go-tmp'
$env:CGO_CFLAGS='-Wno-deprecated-declarations -ID:/services/weknora/tools/msys64/ucrt64/include'
$env:CGO_LDFLAGS='-LD:/services/weknora/tools/duckdb-v1.5.2'
$env:GOLANG_PROTOBUF_REGISTRATION_CONFLICT='warn'
New-Item -ItemType Directory -Force -Path $canaryOutput,$env:GOTMPDIR | Out-Null
Push-Location $canarySource
try {
 & $canaryGo build -p 2 -tags 'sqlite_fts5,duckdb_use_lib' -ldflags '-w -s -X github.com/Tencent/WeKnora/internal/handler.Version=0.8.2 -X github.com/Tencent/WeKnora/internal/handler.Edition=lite -X github.com/Tencent/WeKnora/internal/handler.CommitID=3e8b0bfc80b845b2d4b2ed683994748741450a97+sync-canary-v1' -o (Join-Path $canaryOutput 'weknora.exe') ./cmd/server
 if ($LASTEXITCODE -ne 0) {throw 'Offline candidate build failed'}
 Copy-Item -LiteralPath (Join-Path $canaryTools 'duckdb-v1.5.2/duckdb.dll') -Destination $canaryOutput
 foreach ($canaryDll in @('libgcc_s_seh-1.dll','libstdc++-6.dll','libwinpthread-1.dll')) {
  Copy-Item -LiteralPath (Join-Path $canaryTools ('msys64/ucrt64/bin/'+$canaryDll)) -Destination $canaryOutput
 }
 & $canaryGo test -p 2 -tags 'sqlite_fts5,duckdb_use_lib' ./internal/synccanary ./cmd/sync-canary-fixture -count=1
 if ($LASTEXITCODE -ne 0) {throw 'Native canary verification failed'}
 & $canaryGo test -p 2 -tags 'sqlite_fts5,duckdb_use_lib' ./internal/handler ./internal/router ./internal/container -run 'TestSyncCanary' -count=1
 if ($LASTEXITCODE -ne 0) {throw 'Candidate targeted verification failed'}
 & $canaryGo test -p 2 -tags 'sqlite_fts5,duckdb_use_lib' ./internal/middleware ./internal/types ./internal/router ./internal/handler ./internal/application/repository ./internal/application/service ./internal/agent/tools ./internal/application/repository/retriever/sqlite -run 'Test(ReaderIdentity|RecoveryKeyList|KeywordGuard|LocalKeywordGuardRealHybridSQLite|FixedFiveLocalFTSRepairPlan|FrontendStaticDoesNotIntercept|SyncCanary)' -count=1
 if ($LASTEXITCODE -ne 0) {throw 'Inherited guard integration verification failed'}
 Get-FileHash -LiteralPath (Join-Path $canaryOutput 'weknora.exe') -Algorithm SHA256
} finally {Pop-Location}
