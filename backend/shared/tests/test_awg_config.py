import pytest

from shared.awg_config import (
    AwgFullConfig,
    AwgInterfaceConfig,
    AwgPeerConfig,
    parse_awg_config,
    serialize_awg_config,
)

# Валидные base64-ключи для тестов (ровно 32 байта → 44 символа base64).
_VALID_PRIV = "Wyw1Tr4L/NV0SKMDjNtwhAKgQQkY/NlMXhwRjZrVQ4o="
_VALID_PUB = "k6E1U4ZvkV8Lxay5d8HvPCtHsO0XG6iZzQOvmW+qWrY="
_VALID_PSK = "WhDYtB6K2T++CMJk2t3gd8piDe7FrtoIwI4DUMUof+E="


def test_parse_minimal_valid_config() -> None:
    text = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""
    config = parse_awg_config(text)
    assert config.interface.address == "10.66.66.2/24"
    assert config.interface.private_key == _VALID_PRIV
    assert config.interface.dns is None
    assert config.peer.public_key == _VALID_PUB
    assert config.peer.endpoint == "vpn.example.com:51820"
    assert config.peer.persistent_keepalive is None


def test_parse_amnezia_2_full_config_19_interface_5_peer() -> None:
    """Все 19 параметров [Interface] и 5 параметров [Peer] из дизайн-формата."""
    text = f"""[Interface]
Address = 10.66.66.2/24
DNS = 1.1.1.1, 8.8.8.8
PrivateKey = {_VALID_PRIV}
Jc = 4
Jmin = 50
Jmax = 1000
S1 = 100
S2 = 200
S3 = 50
S4 = 75
H1 = 550935789-1589832211
H2 = 1667329349-1738951753
H3 = 2092343237-2146374126
H4 = 2146579153-2146907010
I1 = <b 0x16030300610100005d>
I2 = <b 0x160303003c020000380303>
I3 = <b 0x160303003c0200003803>
I4 = <b 0x10000080b816bf6df946>
I5 = <b 0x1703030165474554202f>

[Peer]
PublicKey = {_VALID_PUB}
PresharedKey = {_VALID_PSK}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = vpn.example.com:51820
PersistentKeepalive = 25
"""
    config = parse_awg_config(text)
    iface = config.interface
    assert iface.dns == "1.1.1.1, 8.8.8.8"
    assert (iface.jc, iface.jmin, iface.jmax) == (4, 50, 1000)
    assert (iface.s1, iface.s2, iface.s3, iface.s4) == (100, 200, 50, 75)
    # H1-H4 — строки (могут быть диапазонами min-max или одним числом).
    assert iface.h1 == "550935789-1589832211"
    assert iface.h4 == "2146579153-2146907010"
    # I1-I5 — opaque blob'ы (формат `<b 0x...>` в реальных amnezia-конфигах).
    assert iface.i1 == "<b 0x16030300610100005d>"
    assert iface.i5 == "<b 0x1703030165474554202f>"
    peer = config.peer
    assert peer.preshared_key == _VALID_PSK
    assert peer.allowed_ips == "0.0.0.0/0, ::/0"
    assert peer.persistent_keepalive == 25


def test_parse_real_amnezia_conf_with_blob_i_and_range_h() -> None:
    """Регрессия: H1-H4 как диапазоны не должны падать с 'ожидалось целое число',
    I1-I5 как `<b 0x...>` blob'ы тоже не интерпретируются как числа."""
    text = f"""[Interface]
Address = 10.8.1.14/32
PrivateKey = {_VALID_PRIV}
H1 = 550935789-1589832211
I1 = <b 0x5245474953544552207369703a>

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 31.210.171.166:44324
"""
    config = parse_awg_config(text)
    assert config.interface.h1 == "550935789-1589832211"
    assert config.interface.i1 is not None and config.interface.i1.startswith("<b 0x")


def test_parse_skips_comments_and_empty_lines() -> None:
    text = f"""# header comment
[Interface]
# в начале интерфейса

Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}
; semicolon-comment тоже игнорируется

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""
    parsed = parse_awg_config(text)
    assert parsed.interface.address == "10.66.66.2/24"


def test_parse_rejects_missing_interface_section() -> None:
    text = f"""[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""
    with pytest.raises(ValueError, match="Interface"):
        parse_awg_config(text)


def test_parse_rejects_missing_peer_section() -> None:
    text = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}
"""
    with pytest.raises(ValueError, match="Peer"):
        parse_awg_config(text)


def test_parse_rejects_invalid_private_key() -> None:
    text = """[Interface]
Address = 10.66.66.2/24
PrivateKey = obviously-not-a-valid-base64-key

[Peer]
PublicKey = aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""
    with pytest.raises(ValueError):
        parse_awg_config(text)


def test_parse_rejects_invalid_endpoint() -> None:
    text = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = no-port-here
"""
    with pytest.raises(ValueError, match="Endpoint"):
        parse_awg_config(text)


def test_parse_coerces_integer_fields() -> None:
    text = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}
ListenPort = 51820
MTU = 1280
Jc = 7

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
PersistentKeepalive = 30
"""
    parsed = parse_awg_config(text)
    assert parsed.interface.listen_port == 51820
    assert parsed.interface.mtu == 1280
    assert parsed.interface.jc == 7
    assert parsed.peer.persistent_keepalive == 30


def test_parse_rejects_non_integer_for_int_field() -> None:
    text = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}
Jc = not-a-number

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""
    with pytest.raises(ValueError, match="Jc"):
        parse_awg_config(text)


def test_parse_rejects_duplicate_section() -> None:
    text = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}

[Interface]
Address = 10.66.66.3/24

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""
    with pytest.raises(ValueError, match="Дублирующаяся"):
        parse_awg_config(text)


def test_parse_ignores_unknown_keys() -> None:
    """Forward-compat: новые поля будущих версий AmneziaWG не должны ломать парсер."""
    text = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}
SomeFutureField = whatever

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
AnotherFuture = ignored
"""
    parsed = parse_awg_config(text)
    assert parsed.interface.address == "10.66.66.2/24"


def test_serialize_round_trips_minimal() -> None:
    original = AwgFullConfig(
        interface=AwgInterfaceConfig(address="10.66.66.2/24", private_key=_VALID_PRIV),
        peer=AwgPeerConfig(
            public_key=_VALID_PUB,
            allowed_ips="0.0.0.0/0",
            endpoint="vpn.example.com:51820",
        ),
    )
    text = serialize_awg_config(original)
    reparsed = parse_awg_config(text)
    assert reparsed == original


def test_serialize_round_trips_full_amnezia_2() -> None:
    original = AwgFullConfig(
        interface=AwgInterfaceConfig(
            address="10.66.66.2/24",
            dns="1.1.1.1",
            private_key=_VALID_PRIV,
            jc=4,
            jmin=50,
            jmax=1000,
            s1=100,
            s2=200,
            s3=50,
            s4=75,
            h1="550935789-1589832211",
            h2="1667329349-1738951753",
            h3="2092343237-2146374126",
            h4="2146579153-2146907010",
            i1="<b 0x16030300610100005d>",
            i2="<b 0x160303003c020000380303>",
            i3="<b 0x160303003c0200003803>",
            i4="<b 0x10000080b816bf6df946>",
            i5="<b 0x1703030165474554202f>",
        ),
        peer=AwgPeerConfig(
            public_key=_VALID_PUB,
            preshared_key=_VALID_PSK,
            allowed_ips="0.0.0.0/0, ::/0",
            endpoint="vpn.example.com:51820",
            persistent_keepalive=25,
        ),
    )
    text = serialize_awg_config(original)
    reparsed = parse_awg_config(text)
    assert reparsed == original


def test_serialize_excludes_none_fields() -> None:
    config = AwgFullConfig(
        interface=AwgInterfaceConfig(address="10.66.66.2/24", private_key=_VALID_PRIV),
        peer=AwgPeerConfig(
            public_key=_VALID_PUB,
            allowed_ips="0.0.0.0/0",
            endpoint="vpn.example.com:51820",
        ),
    )
    text = serialize_awg_config(config)
    assert "DNS" not in text  # был None
    # MTU теперь имеет default=1280 (безопасный для двойной AWG-обёртки),
    # поэтому в сериализованном выводе он есть. См. NFT-5.
    assert "MTU = 1280" in text
    assert "Jc" not in text
    assert "PersistentKeepalive" not in text


def test_default_mtu_is_1280() -> None:
    """NFT-5 (2026-05-06): MTU=1280 default чтобы двойной AWG-туннель не
    фрагментировался (eth0=1500 − 2×80 outer header = 1340 effective)."""
    interface = AwgInterfaceConfig(address="10.66.66.2/24", private_key=_VALID_PRIV)
    assert interface.mtu == 1280


def test_parse_legacy_config_without_mtu_uses_default() -> None:
    """Старые .conf без MTU при parse применяют Pydantic-default=1280.
    Backward-compat: stored configs без MTU при перезаписи получат явный MTU=1280."""
    text = f"""[Interface]
Address = 10.66.66.2/24
PrivateKey = {_VALID_PRIV}

[Peer]
PublicKey = {_VALID_PUB}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.com:51820
"""
    parsed = parse_awg_config(text)
    assert parsed.interface.mtu == 1280
    # При re-serialize MTU попадает в конфиг.
    assert "MTU = 1280" in serialize_awg_config(parsed)
