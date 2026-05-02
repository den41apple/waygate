import base64
import binascii
import os
import signal
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import NameOID
from loguru import logger

from agent.config import settings
from shared.schemas import AcmeChallenge, TlsApplyResponse, TlsConfig, TlsMode

_CERT_FILENAME = "cert.pem"
_KEY_FILENAME = "key.pem"


class TlsApplyError(RuntimeError):
    """Не удалось применить TLS-конфигурацию."""


def _parse_certificate(*, pem_bytes: bytes) -> tuple[datetime, list[str]]:
    """Возвращает (expires_at, [SAN-домены или CN])."""
    try:
        certificate = x509.load_pem_x509_certificate(pem_bytes, default_backend())
    except ValueError as exc:
        raise TlsApplyError(f"невалидный cert.pem: {exc}") from exc

    expires_at = certificate.not_valid_after_utc
    domains: list[str] = []
    try:
        san_extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        domains.extend(san_extension.value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass
    if not domains:
        common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        domains.extend(value for attr in common_names if isinstance(value := attr.value, str))
    return expires_at, domains


def _signal_granian_reload() -> None:
    """SIGUSR1 на parent-процесс — granian ребрасывает на воркеры и перечитывает TLS.

    Если PID 1 (контейнер с одним процессом) или мы вне systemd — просто логируем
    и полагаемся на следующий restart.
    """
    parent_pid = os.getppid()
    if parent_pid <= 1:
        logger.warning("tls: parent_pid={} — пропускаю SIGUSR1, ожидаю внешний reload", parent_pid)
        return
    try:
        os.kill(parent_pid, signal.SIGUSR1)
        logger.info("tls: отправлен SIGUSR1 в parent_pid={}", parent_pid)
    except OSError as exc:
        logger.warning("tls: не удалось отправить SIGUSR1: {}", exc)


def _ensure_tls_dir() -> Path:
    tls_dir = Path(settings.tls_dir)
    tls_dir.mkdir(parents=True, exist_ok=True)
    return tls_dir


async def _apply_upload(*, config: TlsConfig) -> TlsApplyResponse:
    if config.cert_pem is None or config.key_pem is None:
        raise TlsApplyError("upload-режим: cert_pem и key_pem обязательны")
    try:
        cert_bytes = base64.b64decode(config.cert_pem.encode("ascii"))
        key_bytes = base64.b64decode(config.key_pem.encode("ascii"))
    except (ValueError, binascii.Error) as exc:
        raise TlsApplyError(f"upload-режим: не base64: {exc}") from exc

    expires_at, domains = _parse_certificate(pem_bytes=cert_bytes)
    tls_dir = _ensure_tls_dir()
    cert_path = tls_dir / _CERT_FILENAME
    key_path = tls_dir / _KEY_FILENAME
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)
    cert_path.chmod(0o644)
    key_path.chmod(0o600)
    _signal_granian_reload()
    return TlsApplyResponse(cert_path=str(cert_path), expires_at=expires_at, domains=domains)


async def _apply_path(*, config: TlsConfig) -> TlsApplyResponse:
    if config.cert_path is None or config.key_path is None:
        raise TlsApplyError("path-режим: cert_path и key_path обязательны")
    src_cert = Path(config.cert_path)
    src_key = Path(config.key_path)
    if not src_cert.is_file() or not src_key.is_file():
        raise TlsApplyError(f"path-режим: файлы не найдены ({src_cert}, {src_key})")

    expires_at, domains = _parse_certificate(pem_bytes=src_cert.read_bytes())
    tls_dir = _ensure_tls_dir()
    target_cert = tls_dir / _CERT_FILENAME
    target_key = tls_dir / _KEY_FILENAME
    # Чистим старые цели — могут быть симлинками или файлами от прошлого режима
    if target_cert.is_symlink() or target_cert.exists():
        target_cert.unlink()
    if target_key.is_symlink() or target_key.exists():
        target_key.unlink()
    target_cert.symlink_to(src_cert.resolve())
    target_key.symlink_to(src_key.resolve())
    _signal_granian_reload()
    return TlsApplyResponse(cert_path=str(target_cert), expires_at=expires_at, domains=domains)


async def _apply_acme(*, config: TlsConfig) -> TlsApplyResponse:
    """Заглушка — реальный ACME-клиент пока не реализован.

    План:
      - HTTP-01: временный aiohttp-сервер на :80, выдаёт `/.well-known/acme-challenge/<token>`.
      - DNS-01: Cloudflare/deSEC/Route53 API для TXT-записи.

    Сейчас upload/path работают полноценно, ACME поднимется в отдельной итерации.
    """
    if config.challenge is AcmeChallenge.HTTP01:
        raise TlsApplyError("ACME HTTP-01 пока не реализован — используйте upload или path")
    if config.challenge is AcmeChallenge.DNS01:
        raise TlsApplyError("ACME DNS-01 пока не реализован — используйте upload или path")
    raise TlsApplyError(f"неизвестный ACME-challenge: {config.challenge}")


async def apply_tls(*, config: TlsConfig) -> TlsApplyResponse:
    """Диспетчер: выбирает реализацию по config.mode, применяет, возвращает метаданные."""
    logger.info("tls: применяю режим {} (port={})", config.mode, config.port)
    if config.mode is TlsMode.UPLOAD:
        return await _apply_upload(config=config)
    if config.mode is TlsMode.PATH:
        return await _apply_path(config=config)
    if config.mode is TlsMode.ACME:
        return await _apply_acme(config=config)
    raise TlsApplyError(f"неизвестный TLS-режим: {config.mode}")


def read_current_cert_metadata() -> tuple[datetime, list[str]] | None:
    """Возвращает expires_at и domains если cert.pem существует, иначе None."""
    cert_path = Path(settings.tls_dir) / _CERT_FILENAME
    if not cert_path.exists():
        return None
    try:
        return _parse_certificate(pem_bytes=cert_path.read_bytes())
    except TlsApplyError:
        return None
