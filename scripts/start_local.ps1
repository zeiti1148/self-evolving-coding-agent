$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $projectRoot '.cache\local-server'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$env:LLM_CONFIG_SOURCE = 'project'
$env:LLM_CONFIG_FILE = Join-Path $projectRoot '.env'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$nodePath = (Get-Command node.exe).Source
$services = @(
    @{ Name='backend'; Port=8000; Executable=$pythonPath; Arguments=@('-m','uvicorn','backend.api.main:app','--host','127.0.0.1','--port','8000','--workers','1'); Directory=$projectRoot },
    @{ Name='frontend'; Port=5173; Executable=$nodePath; Arguments=@('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port','5173','--strictPort'); Directory=(Join-Path $projectRoot 'frontend') }
)
foreach ($service in $services) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $service.Port -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Output ($service.Name + ': port ' + $service.Port + ' already in use; left existing process unchanged.')
        continue
    }
    $process = Start-Process -FilePath $service.Executable -ArgumentList $service.Arguments -WorkingDirectory $service.Directory -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory ($service.Name + '.out.log')) -RedirectStandardError (Join-Path $logDirectory ($service.Name + '.err.log')) -PassThru
    @{ pid=$process.Id; executable=$service.Executable; port=$service.Port; directory=$service.Directory } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDirectory ($service.Name + '.json')) -Encoding utf8
    Write-Output ($service.Name + ': started process ' + $process.Id)
}
Write-Output 'Open http://127.0.0.1:5173/#/'
