"""Server-Sent Events (SSE) streaming endpoint for real-time frontend updates"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import asyncio
import json
from datetime import datetime
from src.services.momentum_engine import momentum_engine
from src.utils.logger import setup_logger

logger = setup_logger("sse")

router = APIRouter(prefix="/api", tags=["SSE"])

# Global list of active SSE clients
sse_clients = []

async def token_update_stream():
    """Stream real-time token updates via SSE"""
    last_token_ids = set()
    
    try:
        while True:
            # Get current active tokens
            current_token_ids = set(momentum_engine.token_trades.keys()) if hasattr(momentum_engine, 'token_trades') else set()
            
            # Detect new tokens
            new_tokens = current_token_ids - last_token_ids
            
            if new_tokens:
                for token_id in new_tokens:
                    yield f"data: {json.dumps({'type': 'token_new', 'token_id': token_id})}\n\n"
            
            # Update metrics for all active tokens
            for token_id in current_token_ids:
                metrics = momentum_engine.get_all_metrics(token_id)
                if metrics:
                    yield f"data: {json.dumps({'type': 'metrics_update', 'token_id': token_id, 'metrics': metrics, 'timestamp': datetime.utcnow().isoformat()})}\n\n"
            
            last_token_ids = current_token_ids
            
            # Send every 1 second
            await asyncio.sleep(1)
    
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled")
    except Exception as e:
        logger.error(f"SSE error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

@router.get("/stream")
async def sse_stream():
    """SSE endpoint for real-time frontend updates"""
    return StreamingResponse(
        token_update_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )

@router.post("/signal-event")
async def broadcast_signal(signal_data: dict):
    """Internal endpoint for momentum alerter to broadcast signals"""
    try:
        # This would be called by momentum_alerter when a signal fires
        signal_json = json.dumps({
            "type": "signal_fired",
            "data": signal_data,
            "timestamp": datetime.utcnow().isoformat()
        })
        logger.info(f"Signal broadcasted: {signal_data.get('signal_type', 'unknown')}")
        return {"status": "broadcasted"}
    except Exception as e:
        logger.error(f"Error broadcasting signal: {e}")
        return {"status": "error", "message": str(e)}
