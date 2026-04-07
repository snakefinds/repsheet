@echo off
cd /d "%~dp0"
where pyw >nul 2>&1 && start "" pyw -3 "%~dp0AddPickslyToJson.pyw" && exit /b 0
where pythonw >nul 2>&1 && start "" pythonw "%~dp0AddPickslyToJson.pyw" && exit /b 0
echo Python was not found. Install Python 3 from python.org and check "Add to PATH".
pause
exit /b 1
