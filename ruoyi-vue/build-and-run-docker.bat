@chcp 65001 >nul
@echo off
cd /d "%~dp0"

echo ========================================================
echo  RuoYi AutoTest Platform - Docker Startup
echo ========================================================
echo.

echo [1/3] Checking Docker environment...
docker --version >nul 2>&1
if errorlevel 1 goto DOCKER_ERROR

echo [OK] Docker CLI detected. Checking daemon connection...
docker ps >nul 2>&1
if errorlevel 1 goto DAEMON_ERROR

echo.
echo [2/3] Building and starting Docker containers (MySQL, Redis, Backend)...
docker-compose up --build -d
if errorlevel 1 goto COMPOSE_ERROR

echo.
echo [3/3] Containers started successfully! Current status:
docker-compose ps

echo.
echo ========================================================
echo  Backend Web API:  http://localhost:8080
echo  Gate Channel:     ws://localhost:8765/gate
echo  MySQL Database:   localhost:3306 (root / password)
echo  Redis Cache:      localhost:6379
echo ========================================================
echo.
goto END

:DOCKER_ERROR
echo.
echo [ERROR] Docker command not found. Please install Docker Desktop.
echo.
pause
exit /b 1

:DAEMON_ERROR
echo.
echo ========================================================
echo [ERROR] Docker Desktop service is NOT running!
echo --------------------------------------------------------
echo Please open Docker Desktop from your desktop/start menu,
echo wait until it shows "Engine running" (green icon),
echo then double-click this script again.
echo ========================================================
echo.
pause
exit /b 1

:COMPOSE_ERROR
echo.
echo [ERROR] docker-compose failed to start containers.
echo.
pause
exit /b 1

:END
pause
