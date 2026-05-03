import json
import re
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import ASGIApp, Receive, Scope, Send

from server.auth.session import SessionTokenError, decode_session_token
from server.models import AuditEntry

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "ssh_password",
        "ssh_private_key",
        "cert_pem",
        "key_pem",
        "dns_api_key",
        "token",
        "config_text",  # .conf AWG-клиента — содержит PrivateKey
        "PrivateKey",
        "private_key",
        "PresharedKey",
        "preshared_key",
    },
)
_SERVER_ID_RE = re.compile(r"^/api/v1/servers/(\d+)")
_AUDITED_METHODS = frozenset({"POST", "PATCH", "DELETE", "PUT"})


def redact(payload: Any) -> Any:
    """Заменяет секретные значения на ***. Не модифицирует исходник."""
    if isinstance(payload, dict):
        return {key: ("***" if key in _SENSITIVE_KEYS else redact(value)) for key, value in payload.items()}
    if isinstance(payload, list):
        return [redact(item) for item in payload]
    return payload


def _extract_ip(*, headers: list[tuple[bytes, bytes]]) -> str | None:
    for header_name, header_value in headers:
        if header_name.lower() == b"x-real-ip":
            return header_value.decode("utf-8")
    return None


def _extract_username(*, headers: list[tuple[bytes, bytes]]) -> str | None:
    """Best-effort: достаём username из session-JWT в Authorization-заголовке."""
    for header_name, header_value in headers:
        if header_name.lower() != b"authorization":
            continue
        value = header_value.decode("utf-8", errors="replace")
        if not value.startswith("Bearer "):
            return None
        try:
            claims = decode_session_token(token=value[len("Bearer ") :])
        except SessionTokenError:
            return None
        return claims.username
    return None


def _extract_server_id(*, path: str) -> int | None:
    match = _SERVER_ID_RE.match(path)
    return int(match.group(1)) if match else None


class AuditMiddleware:
    """ASGI-middleware: пишет в audit_entries каждую успешную mutation /api/v1/*.

    user-поле подтягивается из session-JWT в Authorization-заголовке. Тело
    запроса redact'ится по списку sensitive ключей перед записью.
    """

    def __init__(self, app: ASGIApp, *, session_maker: async_sessionmaker[AsyncSession]) -> None:
        # FastAPI вызывает middleware как cls(app, **kwargs) — app должен быть позиционным.
        self.app = app
        self.session_maker = session_maker

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        if method not in _AUDITED_METHODS or not path.startswith("/api/v1/"):
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []

        async def receive_wrapped() -> Any:
            message = await receive()
            if message.get("type") == "http.request":
                body_chunks.append(message.get("body", b""))
            return message

        status_holder: dict[str, int] = {"status": 0}

        async def send_wrapped(message: Any) -> None:
            if message.get("type") == "http.response.start":
                status_holder["status"] = message.get("status", 0)
            await send(message)

        await self.app(scope, receive_wrapped, send_wrapped)

        status_code = status_holder["status"]
        if not (200 <= status_code < 300):
            return

        body_bytes = b"".join(body_chunks)
        payload: Any = None
        if body_bytes:
            try:
                payload = json.loads(body_bytes)
                payload = redact(payload)
            except json.JSONDecodeError:
                payload = None

        headers = scope.get("headers", [])
        try:
            async with self.session_maker() as session:
                session.add(
                    AuditEntry(
                        method=method,
                        path=path,
                        server_id=_extract_server_id(path=path),
                        status_code=status_code,
                        user=_extract_username(headers=headers),
                        ip=_extract_ip(headers=headers),
                        payload=payload,
                    ),
                )
                await session.commit()
        except Exception as exc:
            # audit-log не критичен для работы API — глотаем ошибки чтобы не уронить ответ
            logger.warning("audit: запись не получилась: {}", exc)
