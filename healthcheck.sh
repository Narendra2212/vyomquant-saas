#!/bin/bash
curl -f http://localhost:8000/health/live || exit 1
