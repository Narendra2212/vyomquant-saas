# Railway Deployment Checklist

This document details the configuration steps completed to prepare this repository for deployment on [Railway](https://railway.app/). 

> [!IMPORTANT]  
> All preparation steps have been completed. **Do not execute a deployment** until you have reviewed this checklist and populated the required environment variables in your Railway project dashboard.

## Tasks Completed

- [x] **Verify Docker deployment compatibility**: Updated `startup.sh` to dynamically bind to the `$PORT` environment variable assigned by Railway instead of hardcoding port `8000`.
- [x] **Create `railway.json`**: Created the declarative Railway configuration file. It explicitly sets the builder to Docker, specifies the `startup.sh` command, and defines health checks and restart policies.
- [x] **Create `Procfile`**: Added a standard `Procfile` (`web: ./startup.sh`) as a fallback in case Railway defaults to a Nixpacks builder instead of Docker.
- [x] **Configure environment loading**: The application inherently loads variables from the environment. `startup.sh` actively verifies the presence of required infrastructure secrets (`DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_KEY`) before launching the web server.
- [x] **Configure health endpoint**: Added `/health/live` to `railway.json` so Railway's load balancers can accurately determine when your container is ready to accept traffic.
- [x] **Configure auto restart**: Included `restartPolicyType: ON_FAILURE` and a maximum retry limit in the `railway.json` file.
- [x] **Configure startup command**: Successfully centralized to `./startup.sh` which boots Gunicorn with Uvicorn worker classes.

## Next Steps for the User

1. **Connect Repository**: Link your GitHub repository to your Railway project.
2. **Add Environment Variables**: Go to the Variables tab in Railway and add the following:
   - `DATABASE_URL` (Your primary DB or Supabase Postgres connection string)
   - `SUPABASE_URL` (Your Supabase project URL)
   - `SUPABASE_KEY` (Your Supabase service/anon key)
   - `REDIS_URL` (You can deploy a Redis plugin directly on Railway and copy its internal URL here)
3. **Deploy**: Trigger a manual deploy. The service should automatically detect the `railway.json` configuration, build the Dockerfile, and start securely.
