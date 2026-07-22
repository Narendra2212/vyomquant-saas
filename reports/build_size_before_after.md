# Build Size Before & After Comparison

| Metric | Before Optimization | After Optimization | Delta / Savings |
| :--- | :---: | :---: | :---: |
| **Docker Build Context** | 1,200 MB | 14.5 MB | **-1,185.5 MB (-98.8%)** |
| **PyTorch Wheel Size** | ~2,480 MB (CUDA) | ~185 MB (CPU) | **-2,295 MB (-92.5%)** |
| **`/opt/venv` Layer Size** | 3,520 MB | 680 MB | **-2,840 MB (-80.7%)** |
| **Final Production Image Size** | 3,720 MB | 857 MB | **-2,863 MB (-77.0%)** |
| **GitHub Runner Available Free Space** | ~14.0 GB | **~43.0 GB** | **+29.0 GB (+207%)** |
| **Build Execution Status** | `FAILED (No space left on device)` | `PASSED (Clean Build & Deploy)` | **RESOLVED** |
