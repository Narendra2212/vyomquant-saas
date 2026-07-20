# TEE_DEPLOYMENT_GUIDE

## Docker Configuration
A new Dockerfile has been provisioned specifically for the Trading Execution Engine.

**File**: `Dockerfile.tee`

### Build Command
```bash
docker build -t aerora/tee:latest -f Dockerfile.tee .
```

### Run Command
```bash
docker run -d \
  --name aerora-tee-worker \
  --env-file .env \
  -p 8080:8080 \
  aerora/tee:latest
```

## Migration Instructions
1. **Deploy Redis**: Ensure `REDIS_URL` is accessible by both API and TEE containers.
2. **Deploy TEE**: Launch the `aerora-tee-worker` container in your cluster. Wait for the `/health` endpoint on port 8080 to return HTTP 200.
3. **Enable Routing**: Update the API environment variables: set `USE_TEE=true`. Restart the API container.
4. **Verify**: Deploy a sandbox strategy from the UI. The API should respond immediately, and the `aerora-tee-worker` logs should output `Starting bot for user...`.

## Rollback Instructions
If the TEE fails in production:
1. Revert `USE_TEE=false` on the API containers and restart them. The API will instantly resume executing strategies internally.
