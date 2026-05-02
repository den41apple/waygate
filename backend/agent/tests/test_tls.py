import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent import tls as tls_module
from agent.tls import TlsApplyError, apply_tls
from shared.schemas import AcmeChallenge, TlsConfig, TlsMode


def _generate_self_signed(*, common_name: str = "test.local", domains: list[str] | None = None) -> tuple[bytes, bytes]:
    """Делает self-signed cert + key в PEM. Используется тестами для подачи на upload."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject_name)
        .issuer_name(subject_name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(tz=UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(tz=UTC) + timedelta(days=90))
    )
    if domains:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(domain) for domain in domains]),
            critical=False,
        )
    certificate = builder.sign(private_key=private_key, algorithm=hashes.SHA256())
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_pem, key_pem


@pytest.fixture
def tls_dir(tmp_path, monkeypatch):
    """Подменяем agent.config.settings.tls_dir на tmp_path и блокируем SIGUSR1."""
    monkeypatch.setattr(tls_module.settings, "tls_dir", str(tmp_path))
    monkeypatch.setattr(tls_module, "_signal_granian_reload", lambda: None)
    return tmp_path


async def test_apply_upload_writes_cert_and_key(tls_dir):
    cert_pem, key_pem = _generate_self_signed(common_name="edge.example.com")
    config = TlsConfig(
        mode=TlsMode.UPLOAD,
        cert_pem=base64.b64encode(cert_pem).decode("ascii"),
        key_pem=base64.b64encode(key_pem).decode("ascii"),
    )
    response = await apply_tls(config=config)
    assert (tls_dir / "cert.pem").read_bytes() == cert_pem
    assert (tls_dir / "key.pem").read_bytes() == key_pem
    assert response.cert_path == str(tls_dir / "cert.pem")
    assert response.expires_at > datetime.now(tz=UTC)
    assert "edge.example.com" in response.domains


async def test_apply_upload_rejects_invalid_base64(tls_dir):
    config = TlsConfig(
        mode=TlsMode.UPLOAD,
        cert_pem="not_base64!!",
        key_pem=base64.b64encode(b"key").decode("ascii"),
    )
    with pytest.raises(TlsApplyError, match="не base64"):
        await apply_tls(config=config)


async def test_apply_path_creates_symlinks(tls_dir, tmp_path):
    cert_pem, key_pem = _generate_self_signed(domains=["a.example.com", "b.example.com"])
    src_cert = tmp_path / "src_cert.pem"
    src_key = tmp_path / "src_key.pem"
    src_cert.write_bytes(cert_pem)
    src_key.write_bytes(key_pem)

    config = TlsConfig(mode=TlsMode.PATH, cert_path=str(src_cert), key_path=str(src_key))
    response = await apply_tls(config=config)

    target_cert = tls_dir / "cert.pem"
    target_key = tls_dir / "key.pem"
    assert target_cert.is_symlink()
    assert target_key.is_symlink()
    assert target_cert.readlink() == src_cert.resolve()
    assert set(response.domains) == {"a.example.com", "b.example.com"}


async def test_apply_acme_not_implemented(tls_dir):
    config = TlsConfig(
        mode=TlsMode.ACME,
        domains=["edge.example.com"],
        email="me@example.com",
        challenge=AcmeChallenge.HTTP01,
    )
    with pytest.raises(TlsApplyError, match="HTTP-01"):
        await apply_tls(config=config)
