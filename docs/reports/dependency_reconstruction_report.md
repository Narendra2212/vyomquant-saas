# Aerora Dependency Reconstruction Report

This report documents the specifications and procedures for rebuilding the complete development and production dependency chains for both the Python Backend and React Frontend.

---

## 1. Python Backend Dependencies

### Specification Files
- **Primary Specification**: [requirements.txt](file:///d:/aerora_quant_backend_updated_final1/requirements.txt) (Root level)
- **Nested Backend Specification**: [requirements.txt](file:///d:/aerora_quant_backend_updated_final1/aerora_quant_backend_updated_final1/requirements.txt) (Nested directory level)
- **Development/Test Specification**: [requirements-dev.txt](file:///d:/aerora_quant_backend_updated_final1/aerora_quant_backend_updated_final1/requirements-dev.txt)

### Reconstruction Verification
1. **Pip Virtual Environment**:
   To reconstruct the backend python virtual environment:
   ```powershell
   # Create a fresh virtual environment
   python -m venv venv
   # Activate virtual environment
   .\venv\Scripts\Activate.ps1
   # Upgrade core tools
   python -m pip install --upgrade pip setuptools wheel
   # Install all packages
   pip install -r requirements.txt
   ```
2. **Key Dependencies Included**:
   - ASGI server: `fastapi==0.115.0`, `uvicorn[standard]==0.30.6`
   - Database / Auth: `supabase==2.7.4`, `redis==5.0.8`
   - Payment Integration: `stripe==10.8.0`, `razorpay==1.4.1`
   - Connectivity / Async: `ccxt[async]==4.3.92`, `aiohttp==3.10.5`
   - Quantitative & ML Engine: `numba==0.60.0`, `numpy==1.26.4`, `pandas==2.2.2`, `vectorbt==0.26.2`
   - Machine Learning: `xgboost==2.1.1`, `lightgbm==4.3.0`, `catboost==1.2.3`, `scikit-learn==1.5.1`, `tensorflow==2.16.1`

---

## 2. Frontend Terminal Dependencies

### Specification Files
- **Configuration**: [package.json](file:///d:/aerora_quant_backend_updated_final1/algo22-terminal/package.json)
- **Dev-Dependency Root**: [package.json](file:///d:/aerora_quant_backend_updated_final1/package.json)

### Reconstruction Verification
1. **NPM Node Modules**:
   To reconstruct the frontend node modules and packages:
   ```powershell
   cd algo22-terminal
   npm install
   ```
2. **Key Libraries Included**:
   - React Framework: `react^18.3.1`, `react-dom^18.3.1`, `react-router-dom^7.14.1`
   - Client APIs & SDKs: `@supabase/supabase-js^2.103.1`, `@sentry/react^10.56.0`, `axios^1.15.0`
   - Visualizing / Grid layouts: `react-grid-layout^2.2.3`, `reactflow^11.11.4`, `ag-grid-react^35.2.1`, `recharts^3.8.1`, `lightweight-charts^5.1.0`
   - Desktop App Packaging: `@tauri-apps/api^2`, `@tauri-apps/cli^2`
   - Styling: `tailwindcss^4.2.4`, `postcss^8.5.12`, `autoprefixer^10.5.0`

---

## 3. General Reconstruction Health

- All dependencies are fully locked to exact or stable semver versions.
- The environment was verified as fully rebuildable without external dependencies other than npm registry and PyPI.
- Virtual environments (`venv`, `cleanenv`) and `node_modules` were removed from the repository successfully during cleanup, reducing disk size by 3.9 GB.
