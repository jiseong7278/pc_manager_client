# kill_process.ps1
# PCInspectClient 잔여 프로세스 강제 종료
Get-Process | Where-Object { $_.Path -like "*PCInspectClient*" } |
    ForEach-Object {
        Write-Host "Killing PID $($_.Id): $($_.Path)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
