$ErrorActionPreference = "Stop"
python -m pip install -r requirements-dev.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name HashVeil app.py
Write-Host "Build complete: dist\HashVeil.exe"

