Write-Output "Deleting existing venv..."
if (Test-Path "venv") {
    Remove-Item -Path "venv" -Recurse -Force
}

Write-Output "Creating new venv with Python 3.12..."
py -3.12 -m venv venv

Write-Output "Upgrading pip..."
.\venv\Scripts\python.exe -m pip install --upgrade pip

Write-Output "Installing requirements..."
.\venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Output "Installing math stack..."
.\venv\Scripts\python.exe -m pip install numba vectorbt numpy scipy scikit-learn

Write-Output "Verifying Numba..."
.\venv\Scripts\python.exe -c "import numba; print('NUMBA_PASS')"

Write-Output "Verifying VectorBT..."
.\venv\Scripts\python.exe -c "import vectorbt; print('VECTORBT_PASS')"
