@echo off
chcp 65001 >nul
rem 저장소 루트에서 더블클릭 또는 실행:  experiment\launch\start_server.bat
cd /d "%~dp0\..\.."
if not exist .venv\Scripts\python.exe (
  echo [1/2] 가상환경 만들고 패키지 설치 중... (처음 한 번, 몇 분 걸릴 수 있음)
  python -m venv .venv
  if errorlevel 1 ( echo Python 3.9~3.12가 설치돼 있어야 합니다. & pause & exit /b 1 )
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r experiment\launch\requirements_light.txt
  if errorlevel 1 ( echo 설치 실패. 위 오류를 확인하세요. & pause & exit /b 1 )
)
echo [서버 실행]
set PORT=5001
set HOST=0.0.0.0
.venv\Scripts\python experiment\launch\run_server.py
pause
