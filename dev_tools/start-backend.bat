@echo off
:: Start ALGO22 backend from nested directory structure

echo Starting ALGO22 backend...
echo.

:: Navigate to the actual project directory
cd /d "%~dp0aerora_quant_backend_updated_final1"

:: Set environment
set DEV_MODE=true
set ENV=development
set PYTHONPATH=%CD%

echo Current directory: %CD%
echo PYTHONPATH: %PYTHONPATH%
echo.

:: Start uvicorn
uvicorn backend_app.main:app --host 0.0.0.0 --port 8000 --reload

pause
