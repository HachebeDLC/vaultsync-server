from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from ..dependencies import get_current_user
from ..services.event_notifier import event_notifier

router = APIRouter()

class TestEventRequest(BaseModel):
    message: str = "Test Notification"
    target_device: Optional[str] = None

@router.get("/events")
async def sse_events(
    device_name: Optional[str] = Query(None),
    current_user = Depends(get_current_user)
):
    """
    Establishes a persistent Server-Sent Events (SSE) connection.
    Broadcasts file availability updates in real-time.
    """
    return StreamingResponse(
        event_notifier.generator(current_user['id'], device_name),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no" # Prevent Nginx/proxies from buffering the stream
        }
    )

@router.get("/events/test")
async def send_test_notification_get(
    message: str = Query("Test Notification"),
    target_device: Optional[str] = Query(None),
    current_user = Depends(get_current_user)
):
    """
    Simple GET endpoint to trigger a test notification.
    Example: /api/v1/events/test?message=Hello&target_device=SteamDeck
    """
    payload = {
        "message": message,
        "type": "test_notification"
    }
    await event_notifier.broadcast_to_user(
        current_user['id'], 
        payload, 
        event="test_event",
        target_device=target_device
    )
    return {"message": f"Notification '{message}' queued for user {current_user['id']}"}

@router.post("/events/test")
async def send_test_notification_post(
    body: TestEventRequest,
    current_user = Depends(get_current_user)
):
    """
    Sends a test notification to all of the user's devices, 
    or a specific one if target_device is provided.
    """
    payload = {
        "message": body.message,
        "type": "test_notification"
    }
    await event_notifier.broadcast_to_user(
        current_user['id'], 
        payload, 
        event="test_event",
        target_device=body.target_device
    )
    return {"message": "Notification queued"}
