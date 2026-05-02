from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from loguru import logger
from pydantic import BaseModel

from server.auth.dependencies import require_user
from server.models import User
from server.ws.auth import WsTokenError, sign_ws_token, verify_ws_token
from server.ws.manager import get_manager

router = APIRouter(tags=["ws"])


class WsTokenResponse(BaseModel):
    token: str
    ttl_seconds: int


@router.post("/api/v1/auth/ws-token", response_model=WsTokenResponse)
async def issue_ws_token(user: User = Depends(require_user)) -> WsTokenResponse:
    """Выдаёт короткоживущий JWT для подключения к /ws/events.

    Требует session-Bearer'а; ws-token наследует identity юзера для будущего
    user-scoped audit (когда WS начнёт писать в audit-log).
    """
    ttl_seconds = 3600
    return WsTokenResponse(
        token=sign_ws_token(ttl_seconds=ttl_seconds, username=user.username),
        ttl_seconds=ttl_seconds,
    )


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket, token: str = Query(default="")) -> None:
    """Один общий WebSocket-канал для всех server-side событий.

    Авторизация — только при старте по query-параметру `token=<jwt>`.
    Клиентских сообщений мы не ожидаем; чтение нужно только чтобы поймать
    отключение клиента.
    """
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="нужен query-параметр token")
        return
    try:
        verify_ws_token(token=token)
    except WsTokenError as exc:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc))
        return

    manager = get_manager()
    await manager.connect(websocket=websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("ws: receive_text упал: {}", exc)
    finally:
        await manager.disconnect(websocket=websocket)
