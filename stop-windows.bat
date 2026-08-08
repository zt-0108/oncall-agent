@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ====================================
echo Stopping SuperBizAgent
echo ====================================
echo Project: %CD%
echo.

call :find_docker
if defined DOCKER_CMD (
    set "DOCKER_CONFIG=%CD%\.docker-config"
)

echo [1/6] Stopping FastAPI on port 9900...
call :kill_port 9900
taskkill /FI "WINDOWTITLE eq SuperBizAgent API*" /F >nul 2>&1
echo.

echo [2/6] Stopping Fault Lab on port 9910...
call :kill_port 9910
taskkill /FI "WINDOWTITLE eq Fault Lab*" /F >nul 2>&1
echo.

echo [3/6] Stopping Log MCP on port 8003...
call :kill_port 8003
taskkill /FI "WINDOWTITLE eq CLS MCP Server*" /F >nul 2>&1
echo.

echo [4/6] Stopping Monitor MCP on port 8004...
call :kill_port 8004
taskkill /FI "WINDOWTITLE eq Monitor MCP Server*" /F >nul 2>&1
echo.

echo [5/6] Stopping Prometheus...
if defined DOCKER_CMD (
    "%DOCKER_CMD%" compose -f prometheus-compose.yml down
) else (
    echo [WARN] docker.exe was not found. Skip Prometheus shutdown.
)
echo.

echo [6/6] Stopping Milvus Docker services...
if defined DOCKER_CMD (
    "%DOCKER_CMD%" compose -f vector-database.yml down
    if errorlevel 1 (
        echo [WARN] Docker compose down failed. Check Docker Desktop status.
    ) else (
        echo [OK] Docker services stopped.
    )
) else (
    echo [WARN] docker.exe was not found. Skip Docker shutdown.
)
echo.

echo ====================================
echo SuperBizAgent stopped
echo ====================================
pause
exit /b 0

:kill_port
set "PORT=%~1"
set "FOUND=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    set "FOUND=1"
    echo [INFO] Killing PID %%P on port %PORT%
    taskkill /PID %%P /F >nul 2>&1
)
if "%FOUND%"=="0" echo [INFO] No listener found on port %PORT%.
exit /b 0

:find_docker
set "DOCKER_CMD="
where docker >nul 2>&1
if not errorlevel 1 (
    set "DOCKER_CMD=docker"
    exit /b 0
)
if exist "D:\docker\docker_desktop\resources\bin\docker.exe" (
    set "DOCKER_CMD=D:\docker\docker_desktop\resources\bin\docker.exe"
    exit /b 0
)
if exist "C:\Program Files\Docker\Docker\resources\bin\docker.exe" (
    set "DOCKER_CMD=C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    exit /b 0
)
exit /b 1
