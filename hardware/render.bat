@echo off
REM ==============================================================
REM  Adapix beauty render — double-click to run.
REM
REM  Finds your installed Blender and runs blender_render.py.
REM  Output: adapix_beauty.png in this same folder.
REM ==============================================================

setlocal

cd /d "%~dp0"

set "BLENDER="

REM Try newest first
for %%V in (5.2 5.1 5.0 4.5 4.4 4.3 4.2 4.1 4.0) do (
    if exist "C:\Program Files\Blender Foundation\Blender %%V\blender.exe" (
        if not defined BLENDER set "BLENDER=C:\Program Files\Blender Foundation\Blender %%V\blender.exe"
    )
)

REM Fallback to whatever's on PATH
if not defined BLENDER (
    where blender.exe >nul 2>&1
    if not errorlevel 1 set "BLENDER=blender.exe"
)

if not defined BLENDER (
    echo.
    echo Could not find Blender. Install from https://www.blender.org/
    echo or edit this file and set BLENDER to your blender.exe path.
    echo.
    pause
    exit /b 1
)

echo Using Blender at: %BLENDER%
echo Rendering... this takes 3-8 minutes.
echo.

"%BLENDER%" --background --python "blender_render.py"

echo.
if exist "adapix_beauty.png" (
    echo Done. Opening render...
    start "" "adapix_beauty.png"
) else (
    echo Render did not produce a PNG. Check the messages above.
)

pause
