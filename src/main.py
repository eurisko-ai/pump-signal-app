"""Pump Signal FastAPI Application"""
from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio
from src.config import get_settings
from src.utils.logger import setup_logger
from src.tasks.websocket_scanner import start_websocket_scanner
from src.tasks.housekeeper import start_housekeeper
from src.tasks.trade_tracker import start_trade_tracker
from src.tasks.momentum_alerter import start_momentum_alerter
from src.tasks.image_backfill import backfill_token_images
from src.routers import api, health, frontend, sse

logger = setup_logger("main")
settings = get_settings()

# Global state
scanner_task = None
housekeeper_task = None
trade_tracker_task = None
momentum_alerter_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan events"""
    logger.info("=== Pump Signal App Starting ===")
    
    # Startup
    global scanner_task, housekeeper_task, trade_tracker_task, momentum_alerter_task
    scanner_task = asyncio.create_task(start_websocket_scanner())
    logger.info("WebSocket scanner task started")
    
    housekeeper_task = asyncio.create_task(start_housekeeper())
    logger.info("Housekeeper task started")
    
    # Phase 2: High-frequency momentum detection
    trade_tracker_task = asyncio.create_task(start_trade_tracker())
    logger.info("Trade tracker task started (Phase 2)")
    
    momentum_alerter_task = asyncio.create_task(start_momentum_alerter())
    logger.info("Momentum alerter task started (Phase 2)")
    
    # One-time image backfill for existing tokens (runs in background)
    asyncio.create_task(backfill_token_images())
    logger.info("Image backfill task started")
    
    yield
    
    # Shutdown
    for name, task in [
        ("Scanner", scanner_task),
        ("Housekeeper", housekeeper_task),
        ("Trade tracker", trade_tracker_task),
        ("Momentum alerter", momentum_alerter_task),
    ]:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"{name} task cancelled")
    
    logger.info("=== Pump Signal App Shutdown ===")

# Create FastAPI app
app = FastAPI(
    title="Pump Signal",
    description="Pump.fun token signal scanner with Telegram integration",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers (frontend first so specific routes like /api/tokens/active take priority)
app.include_router(frontend.router)  # Frontend has its own /api prefix - specific routes
app.include_router(sse.router)  # SSE streaming
app.include_router(api.router, prefix="/api", tags=["API"])  # Generic /api routes last
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
