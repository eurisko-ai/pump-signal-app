#!/bin/bash

# Setup script - Initialize pump-signal-app

set -e

echo "=== Pump Signal App Setup ==="

# Check Docker is available
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Please install Docker."
    exit 1
fi

echo "✅ Docker found"

# Create .env from template if needed
if [ ! -f .env ]; then
    echo "Creating .env from template..."
    cp .env.template .env
    echo "⚠️  Edit .env with your Moralis API key and Telegram bot token"
else
    echo "✅ .env already exists"
fi

# Build containers
echo "Building Docker images..."
docker compose build

# Start containers
echo "Starting containers..."
docker compose up -d

# Wait for readiness
echo "Waiting for services to be ready..."
for i in {1..30}; do
    if curl -s http://localhost:8000/ready > /dev/null 2>&1; then
        echo "✅ Services ready!"
        break
    fi
    echo "Attempt $i/30..."
    sleep 2
done

# Verify deployment
echo "Verifying deployment..."
if docker compose ps | grep -q "pump-signal-app.*Up"; then
    echo "✅ App container running"
else
    echo "❌ App container not running"
    docker compose logs app | tail -20
    exit 1
fi

if docker compose ps | grep -q "pump-signal-db.*Up"; then
    echo "✅ Database container running"
else
    echo "❌ Database container not running"
    exit 1
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Edit .env with your API keys"
echo "2. Restart: docker compose restart app"
echo "3. View logs: docker compose logs -f app"
echo "4. Test API: curl http://localhost:8000/health"
echo ""
