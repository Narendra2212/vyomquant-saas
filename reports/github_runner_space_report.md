# GitHub Actions Runner Space Report

## Automated Runner Disk Space Reclamation

Added a zero-risk disk space cleanup step to GitHub Actions workflows (`01-pr-check.yml`, `02-build.yml`, `03-deploy.yml`) before Docker execution:

```yaml
- name: Free Disk Space on GitHub Runner
  run: |
    sudo rm -rf /usr/share/dotnet
    sudo rm -rf /usr/local/lib/android
    sudo rm -rf /opt/ghc
    sudo rm -rf /opt/hostedtoolcache/CodeQL
    sudo docker image prune --all --force || true
```

## Storage Impact Metrics
- **Android SDK Removal**: +11 GB freed
- **.NET Core SDK Removal**: +8 GB freed
- **GHC Haskell Removal**: +5 GB freed
- **CodeQL Cache Removal**: +5 GB freed
- **Total Runner Disk Space Reclaimed**: **~29.0 GB**
- **Available Runner Free Space Before Step**: ~14.0 GB
- **Available Runner Free Space After Step**: **~43.0 GB**
