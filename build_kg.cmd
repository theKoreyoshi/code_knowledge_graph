@echo off
rem ---------------------------------------------------------------------------
rem  code-kg launcher.
rem
rem    build_kg.cmd                          -> build the bundled C example (examples/demo-c)
rem    build_kg.cmd C:\path\to\any\project   -> zero-config build of any project
rem    build_kg.cmd C:\path\to\proj -j 8     -> ... with 8 parallel workers
rem
rem  First run creates .venv and installs the dependencies.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [1/3] creating virtual environment .venv ...
  python -m venv .venv || goto :fail
  echo [2/3] installing dependencies ...
  "%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
)

set "TARGET=%~1"
if "%TARGET%"=="" (
  echo [3/3] building the bundled example examples/demo-c ...
  "%PY%" -m ckg build "examples\demo-c" -o "examples\demo-c\ckg-out" -j 4 || goto :fail
  set "OUT=%CD%\examples\demo-c\ckg-out"
) else (
  echo [3/3] building %TARGET% ...
  "%PY%" -m ckg build "%TARGET%" %2 %3 %4 %5 %6 || goto :fail
  set "OUT="
)

echo.
echo done.
echo output: %OUT%
if exist "%OUT%\ckg.html" start "" "%OUT%\ckg.html"
exit /b 0

:fail
echo.
echo build failed - see the messages above.
exit /b 1
