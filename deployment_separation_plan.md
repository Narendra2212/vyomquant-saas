# Deployment Separation Plan

## BACKEND PACKAGE
- Directories: `backend_app/api`, `backend_app/core`, `backend_app/services`
- Startup: `./startup.sh`
- Docker: `python:3.11-slim`
- Railway: Highly Compatible

## CONNECTION PACKAGE
- Directories: `connection_layer/exchanges`, `connection_layer/websocket`
- Startup: `python -m connection_layer.main`
- Docker: `python:3.11-slim`
- Railway: Compatible (Worker Dyno)
