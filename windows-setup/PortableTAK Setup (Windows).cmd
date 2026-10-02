@echo off
rem Make a PortableTAK USB drive on this Windows computer.
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\PortableTAK-Setup.ps1"
