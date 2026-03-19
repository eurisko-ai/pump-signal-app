"""Pump Signal FastAPI Application"""
from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio
from src.config import get_settings
from src.utils.logger import setup_logger
from src.tasks.websocket_scanner import start_websocket_scanner
from src.tasks.housekeeper import start_housekeeper
from src.routers import api, health

logger = setup_logger("main")
settings = get_settings()

# Global state
scanner_task = None
housekeeper_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan events"""
    logger.info("=== Pump Signal App Starting ===")
    
    # Startup
    global scanner_task, housekeeper_task
    scanner_task = asyncio.create_task(start_websocket_scanner())
    logger.info("WebSocket scanner task started")
    
    housekeeper_task = asyncio.create_task(start_housekeeper())
    logger.info("Housekeeper task started")
    
    yield
    
    # Shutdown
    if scanner_task:
        scanner_task.cancel()
        try:
            await scanner_task
        except asyncio.CancelledError:
            logger.info("Scanner task cancelled")
    
    if housekeeper_task:
        housekeeper_task.cancel()
        try:
            await housekeeper_task
        except asyncio.CancelledError:
            logger.info("Housekeeper task cancelled")
    
    logger.info("=== Pump Signal App Shutdown ===")

# Create FastAPI app
app = FastAPI(
    title="Pump Signal",
    description="Pump.fun token signal scanner with Telegram integration",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers
app.include_router(api.router, prefix="/api", tags=["API"])
app.include_router(health.router, tags=["Health"])

@app.get("/")
async def root():
    return {"message": "Pump Signal API", "status": "online"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.fastapi_host,
        port=settings.fastapi_port,
        reload=(settings.fastapi_env == "development")
    )
