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

# Global event queue for market cap updates (consumed by all SSE streams)
_mc_update_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)


async def broadcast_market_cap_update(token_id: int, market_cap_usd: float):
    """Push a market cap update to all SSE clients via shared queue."""
    try:
        _mc_update_queue.put_nowait({
            "type": "market_cap_update",
            "token_id": token_id,
            "market_cap": market_cap_usd,
            "timestamp": datetime.utcnow().isoformat(),
        })
    except asyncio.QueueFull:
        # Drop oldest if queue is full
        try:
            _mc_update_queue.get_nowait()
            _mc_update_queue.put_nowait({
                "type": "market_cap_update",
                "token_id": token_id,
                "market_cap": market_cap_usd,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception:
            pass


async def token_update_stream():
    """Stream real-time token updates via SSE"""
    last_token_ids = set()
    tick = 0
    
    try:
        while True:
            # Get current active tokens from momentum engine's internal buffers
            current_token_ids = set(momentum_engine._buffers.keys())
            
            # Log status every 5 ticks
            if tick % 5 == 0:
                logger.info(f"SSE tick {tick}: {len(current_token_ids)} tokens in momentum engine")
            
            # Detect new tokens
            new_tokens = current_token_ids - last_token_ids
            
            if new_tokens:
                logger.info(f"SSE: {len(new_tokens)} new tokens detected")
                for token_id in new_tokens:
                    yield f"data: {json.dumps({'type': 'token_new', 'token_id': token_id, 'timestamp': datetime.utcnow().isoformat()})}\n\n"
            
            # Drain market cap update queue
            mc_updates = {}
            while not _mc_update_queue.empty():
                try:
                    update = _mc_update_queue.get_nowait()
                    # Keep only latest per token_id
                    mc_updates[update["token_id"]] = update
                except asyncio.QueueEmpty:
                    break
            
            # Send batched market cap updates
            for update in mc_updates.values():
                yield f"data: {json.dumps(update)}\n\n"
            
            last_token_ids = current_token_ids
            tick += 1
            
            # Send every 1 second
            await asyncio.sleep(1)
    
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled")
    except Exception as e:
        logger.error(f"SSE error: {e}", exc_info=True)
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
