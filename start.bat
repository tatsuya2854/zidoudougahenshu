@echo off
REM Creator DNA Editor 起動スクリプト（Windows）。ダブルクリックで起動。実体は launcher\creator_dna_editor.py
cd /d "%~dp0"
where python >nul 2>&1 || (echo Python が見つかりません。python.org から 3.11 以上を入れてください。 & pause & exit /b 1)
python launcher\creator_dna_editor.py --foreground %*
if errorlevel 1 pause
