@echo off
chcp 65001 > nul
title 패킹봇 실행기

cd /d "%~dp0"

echo ==========================================
echo              패킹봇 실행
echo ==========================================
echo.

if exist "text_order_runner.py" (
    goto RUNNER_OK
) else (
    echo [오류] text_order_runner.py 파일을 찾을 수 없습니다.
    echo 이 배치파일을 text_order_runner.py 와 같은 폴더에 넣어주세요.
    echo.
    pause
    exit /b
)

:RUNNER_OK
where py >nul 2>nul
if %errorlevel%==0 (
    py text_order_runner.py
    goto END
)

where python >nul 2>nul
if %errorlevel%==0 (
    python text_order_runner.py
    goto END
)

echo [오류] Python 실행 파일을 찾지 못했습니다.
echo.
echo 아래 중 하나가 설치되어 있어야 합니다.
echo - py
echo - python
echo.
echo Python 설치 후 다시 실행해주세요.
echo.
pause
exit /b

:END
echo.
echo ==========================================
echo 실행이 끝났습니다. 아무 키나 누르면 창이 닫힙니다.
echo ==========================================
pause > nul