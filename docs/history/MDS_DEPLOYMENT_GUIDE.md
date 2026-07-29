# MDS_DEPLOYMENT_GUIDE

## Deployment Configuration

The Market Data Service utilizes its own Docker container and should be deployed alongside the Trading Execution Engine cluster.

### Dockerfile
**File**: `Dockerfile.mds`

### Build Command
```bash
docker build -t aerora/mds:latest -f Dockerfile.mds .
```

### Run Command
*Note: You only need ONE instance of the MDS running. High Availability (HA) for MDS will be handled via active-passive failover in a future sprint, but for now, 1 node is sufficient.*
```bash
docker run -d \
  --name aerora-mds \
  --env-file .env \
  -p 8081:8081 \
  aerora/mds:latest
```

## Migration Steps
1. **Boot MDS**: Start the MDS container. Verify the `8081/health` endpoint is active.
2. **Update TEE Cluster**: Rolling-restart the TEE containers. When the TEE `DataSeekingEngine` boots, it will now automatically publish `mds:commands` to wake up the MDS.
3. **Verify Redis Traffic**: You can run `redis-cli monitor | grep mds:data` to verify that normalized JSON payloads are aggressively flowing into the local cache layer.
