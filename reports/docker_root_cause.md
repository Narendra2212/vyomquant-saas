# Root Cause Analysis: GitHub Actions Runner Disk Space Failure

## Problem Statement
During container build in GitHub Actions, the runner fails with:
`"no space left on device" during COPY --from=builder /opt/venv /opt/venv`

## Primary Root Causes Identified

### 1. PyTorch Default CUDA GPU Binary Weight (~2.5 GB)
When `pip install -r requirements.txt` ran without specifying PyTorch's CPU-only index (`https://download.pytorch.org/whl/cpu`), `pip` defaulted to PyPI's CUDA-enabled PyTorch wheel (`torch==2.3.1`), which pulls ~2.5 GB of NVIDIA CUDA/cuDNN binaries. When extracted inside `/opt/venv`, it consumed **3.52 GB**.

### 2. Multi-Stage Duplication in Docker Storage (~7.04 GB)
In multi-stage Docker builds without BuildKit layer pruning or cache cleanups:
- The builder stage creates `/opt/venv` (**3.52 GB**).
- The intermediate container storage maintains the builder layer (**3.52 GB**).
- The `COPY --from=builder /opt/venv /opt/venv` step duplicates the 3.52 GB directory into the production image layer.
- Total Docker storage consumed by venv during build: **~7.04 GB**.

### 3. Uncleaned GitHub Runner Pre-installed Software (~25+ GB)
GitHub-hosted `ubuntu-latest` runners come preloaded with ~35 GB of pre-installed toolchains:
- Android SDKs (`/usr/local/lib/android`): ~11 GB
- .NET SDKs (`/usr/share/dotnet`): ~8 GB
- Haskell Toolchain (`/opt/ghc`): ~5 GB
- CodeQL (`/opt/hostedtoolcache/CodeQL`): ~5 GB

Because only ~14 GB of usable free disk space remains on default runner root partitions, accumulating builder layers (7+ GB) + unoptimized build context (1.2 GB) + Docker daemon overhead caused disk depletion at the exact `COPY --from=builder` instruction.
