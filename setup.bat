@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set LOG=%~dp0setup.log
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
rem 장치 강제: setup.bat cpu / setup.bat cuda / setup.bat xpu
set TORCH_KIND=%1
echo [setup] %date% %time% > "%LOG%"

echo ============================================
echo  docling-md 설치 (PDF → Markdown, Claude 데스크톱 연동)
echo  자세한 로그: setup.log
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [오류] Python이 없어요. https://www.python.org 에서 설치할 때
  echo        "Add python.exe to PATH"를 꼭 체크해주세요.
  echo [FAIL] python not found >> "%LOG%"
  pause
  exit /b 1
)
python --version >> "%LOG%" 2>&1

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo [오류] Python 3.10 이상이 필요해요.
  echo [FAIL] python too old >> "%LOG%"
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] 가상환경 만드는 중...
  echo [1/5] venv >> "%LOG%"
  python -m venv .venv >> "%LOG%" 2>&1 || goto :fail
)
set PY=.venv\Scripts\python.exe

echo [2/5] GPU 확인 후 맞는 PyTorch 설치 중... (NVIDIA=cuda, Intel 내장/Arc=xpu, 없으면 cpu)
echo [2/5] torch >> "%LOG%"
%PY% -m pip install --upgrade pip >> "%LOG%" 2>&1 || goto :fail
%PY% install_torch.py %TORCH_KIND% >> "%LOG%" 2>&1 || goto :fail
for /f "usebackq delims=" %%L in (`findstr /l /b /c:"[torch]" "%LOG%"`) do echo       %%L

echo [3/5] Docling, MCP 설치 중... (몇 분)
echo [3/5] docling >> "%LOG%"
%PY% -m pip install -r requirements.txt >> "%LOG%" 2>&1 || goto :fail

echo [4/5] 샘플 PDF 변환 테스트 (최초 1회 모델 다운로드)
echo [4/5] sample convert >> "%LOG%"
%PY% server.py convert sample.pdf --md --force >> "%LOG%" 2>&1 || goto :fail
echo       GPU / CPU 속도 비교 중...
%PY% server.py bench sample.pdf >> "%LOG%" 2>&1 || goto :fail
for /f "usebackq delims=" %%L in (`findstr /l /b /c:"[bench]" "%LOG%"`) do echo       %%L

echo [5/5] Claude 데스크톱 앱에 등록 중...
echo [5/5] register >> "%LOG%"
%PY% register_claude.py >> "%LOG%" 2>&1 || goto :fail

echo [DONE] >> "%LOG%"
echo.
echo 설치 끝! Claude 데스크톱 앱을 트레이까지 완전히 종료한 뒤 다시 켜세요.
echo 한국어 스캔본도 쓸 거면:  .venv\Scripts\python.exe -m pip install easyocr
pause
exit /b 0

:fail
echo [FAIL] >> "%LOG%"
echo.
echo [오류] setup.log 를 Claude에게 보여주면 고쳐줄 거예요.
pause
exit /b 1
