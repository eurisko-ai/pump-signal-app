#!/bin/bash

# Reset database (wipe and recreate)

echo "⚠️  This will DELETE all data from the database"
read -p "Are you sure? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled"
    exit 1
fi

echo "Stopping containers..."
docker compose down -v

echo "Removing volume..."
docker volume rm pump-signal-app_pump_signal_db 2>/dev/null || true

echo "Starting fresh..."
docker compose up -d

echo "Waiting for database..."
sleep 10

echo "✅ Database reset complete"
docker compose ps
