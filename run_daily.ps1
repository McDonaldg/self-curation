$root = "C:\Users\Masahiro\projects\SelfCuration\self-curation"
$python = "C:\Users\Masahiro\Anaconda3\envs\selfcuration\python.exe"
$logDir = Join-Path $root "logs"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

"started {0}" -f (Get-Date) | Out-File -FilePath (Join-Path $logDir "_marker.log") -Encoding utf8

try {
    $env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
    $logFile = Join-Path $logDir ("{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))
    Set-Location $root
    & $python main.py *> $logFile 2>&1
    "exit code: $LASTEXITCODE" | Out-File -FilePath (Join-Path $logDir "_marker.log") -Append -Encoding utf8
}
catch {
    $_ | Out-File -FilePath (Join-Path $logDir "_error.log") -Append -Encoding utf8
}
