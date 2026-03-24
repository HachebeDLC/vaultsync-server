from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from ..dependencies import get_current_user
from ..services.event_notifier import event_notifier

router = APIRouter()

@router.get("/events")
async def sse_events(current_user = Depends(get_current_user)):
    """
    Establishes a persistent Server-Sent Events (SSE) connection.
    Broadcasts file availability updates in real-time.
    """
    return StreamingResponse(
        event_notifier.generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no" # Prevent Nginx/proxies from buffering the stream
        }
    )
