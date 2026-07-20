# Deployment Snapshot Report

## Snapshot

| Field | Value |
|---|---|
| Branch | `deployment-v1` |
| Commit Hash | `d8a9f68b2593b5dcb83c65bab12733c0812ca4aa` |
| Commit Message | Production deployment candidate |
| Total Files | 923 |
| Python Files | 370 |
| Frontend Files (JS/TS/HTML/CSS) | 5 |
| Total HTTP Routes | 175 |
| Total WebSocket Endpoints | 9 |
| Total SQLAlchemy Models (Base subclasses) | 20 |

## Rollback Instructions

```bash
# Return to certified branch
git checkout release/cloud-beta-v1

# Or restore specific commit
git checkout d8a9f68b2593b5dcb83c65bab12733c0812ca4aa

# Discard all changes on deployment-v1 branch
git checkout deployment-v1
git reset --hard d8a9f68b2593b5dcb83c65bab12733c0812ca4aa
```

## Certified Source
> [!IMPORTANT]
> The certified production source lives on `release/cloud-beta-v1` at commit `d8a9f68b2`. **Never modify this branch.**
