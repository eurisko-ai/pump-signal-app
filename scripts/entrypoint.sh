#!/bin/bash
set -e

echo "=== Pump Signal App Startup ==="

# Wait for PostgreSQL to be ready (using psql)
echo "Waiting for PostgreSQL..."
max_attempts=30
attempt=0
until PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -c "SELECT 1" 2>/dev/null; do
  attempt=$((attempt+1))
  if [ $attempt -eq $max_attempts ]; then
    echo "PostgreSQL connection failed after 30 attempts"
    exit 1
  fi
  echo "Attempt $attempt/$max_attempts..."
  sleep 1
done
echo "PostgreSQL is ready!"

# Run migrations
echo "Running database migrations..."
python3 /app/scripts/run_migrations.py

# Start FastAPI app
echo "Starting FastAPI application..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
