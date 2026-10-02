@echo off
rem Start the PortableTAK server on this Windows computer from this drive.
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0.portabletak\windows\PortableTAK.ps1" start
