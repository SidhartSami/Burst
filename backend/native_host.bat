@echo off
:: Burst Native Messaging Host Launcher
:: This wrapper is needed because Chrome requires a binary or .bat as the host path.
:: It launches the Python script bundled alongside Burst.
pythonw.exe "%~dp0native_host.py"
