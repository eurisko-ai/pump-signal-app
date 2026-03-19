#!/bin/bash

# Tail logs from running containers

if [ $# -eq 0 ]; then
    # Follow both app and db logs
    docker compose logs -f
elif [ "$1" = "app" ]; then
    docker compose logs -f app
elif [ "$1" = "db" ]; then
    docker compose logs -f postgres
elif [ "$1" = "health" ]; then
    tail -f /var/log/pump-signal/health.log 2>/dev/null || echo "Health log not found yet"
else
    docker compose logs -f "$1"
fi
