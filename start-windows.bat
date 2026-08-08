@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ====================================
echo Starting SuperBizAgent
echo ====================================
echo Project: %CD%
echo.

call :find_docker
if not defined DOCKER_CMD (
    echo [ERROR] docker.exe was not found.
    echo Add Docker to PATH, or install Docker Desktop.
    pause
    exit /b 1
)

set "DOCKER_CONFIG=%CD%\.docker-config"
if not exist "%DOCKER_CONFIG%" mkdir "%DOCKER_CONFIG%" >nul 2>&1

if not exist "logs" mkdir "logs" >nul 2>&1
if not exist "uploads" mkdir "uploads" >nul 2>&1
if not exist "volumes" mkdir "volumes" >nul 2>&1

echo [1/9] Checking Docker Engine...
"%DOCKER_CMD%" ps >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Engine is not available.
    echo Start Docker Desktop first, then run this script again.
    pause
    exit /b 1
)
echo [OK] Docker Engine is available.
echo.

echo [2/9] Checking Python virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] .venv not found. Creating environment...
    where uv >nul 2>&1
    if not errorlevel 1 (
        uv --cache-dir .uv-cache sync
    ) else (
        python -m venv .venv
        if errorlevel 1 (
            echo [ERROR] Failed to create .venv. Install Python 3.11+ first.
            pause
            exit /b 1
        )
        .venv\Scripts\python.exe -m pip install --upgrade pip
        .venv\Scripts\python.exe -m pip install -e .
    )
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )
)
set "PYTHON_CMD=.venv\Scripts\python.exe"
echo [OK] Python environment is ready.
echo.

echo [3/9] Checking DASHSCOPE_API_KEY...
set "DASHSCOPE_KEY="
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="DASHSCOPE_API_KEY" set "DASHSCOPE_KEY=%%B"
    )
)
if not defined DASHSCOPE_KEY (
    echo [ERROR] DASHSCOPE_API_KEY is missing in .env.
    pause
    exit /b 1
)
echo !DASHSCOPE_KEY! | findstr /i "your-api-key" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] DASHSCOPE_API_KEY still looks like a placeholder.
    pause
    exit /b 1
)
echo [OK] DASHSCOPE_API_KEY is configured.
echo.

echo [4/9] Preparing required Docker images...
call :ensure_image "quay.io/coreos/etcd:v3.5.18" "quay.io/coreos/etcd:v3.5.18"
if errorlevel 1 goto docker_image_failed

call :ensure_image "minio/minio:RELEASE.2023-03-20T20-16-18Z" "quay.io/minio/minio:RELEASE.2023-03-20T20-16-18Z"
if errorlevel 1 goto docker_image_failed

call :ensure_image "milvusdb/milvus:v2.5.10" "registry-1.docker.io/milvusdb/milvus:v2.5.10"
if errorlevel 1 goto docker_image_failed
echo [OK] Required Docker images are available.
echo.

echo [5/9] Starting Milvus services...
"%DOCKER_CMD%" compose -f vector-database.yml up -d etcd minio standalone
if errorlevel 1 (
    echo [ERROR] Failed to start Milvus services.
    pause
    exit /b 1
)

echo [INFO] Waiting for milvus-standalone health check...
for /l %%I in (1,1,60) do (
    "%DOCKER_CMD%" ps --filter "name=milvus-standalone" --filter "health=healthy" --format "{{.Names}}" | findstr /i "milvus-standalone" >nul 2>&1
    if not errorlevel 1 goto milvus_ready
    timeout /t 2 /nobreak >nul
)
echo [ERROR] Milvus did not become healthy in time.
"%DOCKER_CMD%" ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
pause
exit /b 1

:milvus_ready
echo [OK] Milvus is healthy.
echo.

echo [6/9] Starting Prometheus...
"%DOCKER_CMD%" compose -f prometheus-compose.yml up -d prometheus
if errorlevel 1 (
    echo [ERROR] Failed to start Prometheus.
    pause
    exit /b 1
)
for /l %%I in (1,1,30) do (
    curl -fsS http://127.0.0.1:9090/-/ready >nul 2>&1
    if not errorlevel 1 goto prometheus_ready
    timeout /t 2 /nobreak >nul
)
echo [ERROR] Prometheus did not become ready in time.
pause
exit /b 1

:prometheus_ready
echo [OK] Prometheus is ready.
echo.

echo [7/9] Starting Fault Lab, MCP services and FastAPI...
netstat -ano | findstr /R /C:":9910 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port 9910 is already listening. Skipping Fault Lab.
) else (
    echo [INFO] Starting Fault Lab on port 9910...
    start "Fault Lab" /min "%PYTHON_CMD%" -m uvicorn fault_lab.app:app --host 127.0.0.1 --port 9910
    timeout /t 2 /nobreak >nul
)

netstat -ano | findstr /R /C:":8003 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port 8003 is already listening. Skipping CLS MCP Server.
) else (
    echo [INFO] Starting CLS MCP Server on port 8003...
    start "CLS MCP Server" /min "%PYTHON_CMD%" mcp_servers\cls_server.py
    timeout /t 2 /nobreak >nul
)

netstat -ano | findstr /R /C:":8004 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port 8004 is already listening. Skipping Monitor MCP Server.
) else (
    echo [INFO] Starting Monitor MCP Server on port 8004...
    start "Monitor MCP Server" /min "%PYTHON_CMD%" mcp_servers\monitor_server.py
    timeout /t 2 /nobreak >nul
)

netstat -ano | findstr /R /C:":9900 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] Port 9900 is already listening. Skipping SuperBizAgent API.
) else (
    echo [INFO] Starting SuperBizAgent API on port 9900...
    start "SuperBizAgent API" /min "%PYTHON_CMD%" -m uvicorn app.main:app --host 127.0.0.1 --port 9900
    timeout /t 2 /nobreak >nul
)
echo.

echo [8/9] Waiting for service health checks...
for /l %%I in (1,1,30) do (
    curl -fsS http://127.0.0.1:9910/health >nul 2>&1
    if not errorlevel 1 goto fault_lab_ready
    timeout /t 2 /nobreak >nul
)
echo [ERROR] Fault Lab did not become healthy in time.
pause
exit /b 1

:fault_lab_ready
echo [OK] Fault Lab is healthy.
echo [INFO] Waiting for FastAPI health check...
for /l %%I in (1,1,30) do (
    curl -fsS http://localhost:9900/health >nul 2>&1
    if not errorlevel 1 goto api_ready
    timeout /t 2 /nobreak >nul
)
echo [ERROR] FastAPI did not become healthy in time.
echo Check logs or the SuperBizAgent API window.
pause
exit /b 1

:api_ready
echo [OK] FastAPI is healthy.
echo.

echo [9/9] Synchronizing changed aiops-docs...
if /i "%RAG_SYNC_MODE%"=="skip" (
    echo [INFO] RAG synchronization skipped because RAG_SYNC_MODE=skip.
) else (
    set "RAG_SYNC_ARGS="
    if /i "%RAG_SYNC_MODE%"=="force" set "RAG_SYNC_ARGS=--force"
    "%PYTHON_CMD%" -m app.cli.sync_aiops_docs --docs-dir aiops-docs --manifest volumes\aiops-docs-manifest.json --upload-url http://127.0.0.1:9900/api/upload --timeout 120 !RAG_SYNC_ARGS!
    if errorlevel 1 echo [WARN] Some knowledge documents were not synchronized. They will be retried next time.
)
echo.

echo ====================================
echo SuperBizAgent started
echo ====================================
echo Web UI:   http://localhost:9900
echo API Docs: http://localhost:9900/docs
echo Health:   http://localhost:9900/health
echo Fault Lab: http://localhost:9910/health
echo Prometheus: http://localhost:9090
echo.
echo Stop services with: stop-windows.bat
echo ====================================
pause
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

:ensure_image
set "TARGET_IMAGE=%~1"
set "SOURCE_IMAGE=%~2"
"%DOCKER_CMD%" image inspect "%TARGET_IMAGE%" >nul 2>&1
if not errorlevel 1 exit /b 0

echo [INFO] Pulling %SOURCE_IMAGE%
"%DOCKER_CMD%" pull "%SOURCE_IMAGE%"
if errorlevel 1 exit /b 1

if /i not "%TARGET_IMAGE%"=="%SOURCE_IMAGE%" (
    "%DOCKER_CMD%" tag "%SOURCE_IMAGE%" "%TARGET_IMAGE%"
    if errorlevel 1 exit /b 1
)
exit /b 0

:docker_image_failed
echo [ERROR] Failed to prepare Docker images.
echo If this is a registry mirror digest error, disable Docker registry mirrors and try again.
pause
exit /b 1
