# Startet den lokalen Dev-Server sauber (beendet alte Prozesse zuerst)
Write-Host "Beende alte uvicorn/Python-Prozesse auf Port 8000..." -ForegroundColor Yellow
$pids = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
foreach ($p in $pids) {
    if ($p -gt 0) {
        Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        Write-Host "  PID $p beendet."
    }
}
Start-Sleep -Milliseconds 500
Write-Host "Starte uvicorn..." -ForegroundColor Green
& .\.venv\Scripts\uvicorn.exe app.main:app --reload
