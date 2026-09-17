@echo off
REM Creator DNA Editor 起動スクリプト（Windows）。ダブルクリックで起動。
cd /d "%~dp0"
where python >nul 2>&1 || (echo Python が見つかりません。python.org から 3.11 以上を入れてください。 & pause & exit /b 1)
where ffmpeg >nul 2>&1 || (echo ffmpeg が見つかりません。README の手順で入れて PATH を通してください。 & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
if not exist backend\.venv (
  echo Python 環境を作成中…
  python -m venv backend\.venv
  backend\.venv\Scripts\pip install -q --upgrade pip
  backend\.venv\Scripts\pip install -q -r backend\requirements.txt
)
if not exist frontend\dist\index.html (
  where npm >nul 2>&1 && (cd frontend && npm install --silent && npm run build --silent && cd ..)
)
echo 起動中… ブラウザが自動で開きます（開かなければ http://127.0.0.1:8765/ ）
cd backend
.venv\Scripts\python -m app
pause
