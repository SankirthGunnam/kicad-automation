@echo off
setlocal
set MINGW=C:\msys64\mingw64\bin
set PATH=%MINGW%;%PATH%

echo Building astar.dll with MinGW g++...
g++ -O2 -shared -o astar.dll astar.cpp -static-libgcc -static-libstdc++ -Wl,-Bstatic -lpthread -Wl,-Bdynamic

if %ERRORLEVEL% EQU 0 (
    echo Build successful: astar.dll
    if not exist libwinpthread-1.dll (
        copy "%MINGW%\libwinpthread-1.dll" libwinpthread-1.dll >nul
        echo Copied libwinpthread-1.dll
    )
) else (
    echo Build FAILED.
)
endlocal
