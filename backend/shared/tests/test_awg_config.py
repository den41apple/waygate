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
H1 = 1
H2 = 2
H3 = 3
H4 = 4
I1 = pattern1
I2 = pattern2
I3 = pattern3
I4 = pattern4
I5 = pattern5

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
    assert (iface.h1, iface.h2, iface.h3, iface.h4) == (1, 2, 3, 4)
    assert (iface.i1, iface.i2, iface.i3, iface.i4, iface.i5) == (
        "pattern1",
        "pattern2",
        "pattern3",
        "pattern4",
        "pattern5",
    )
    peer = config.peer
    assert peer.preshared_key == _VALID_PSK
    assert peer.allowed_ips == "0.0.0.0/0, ::/0"
    assert peer.persistent_keepalive == 25


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
            h1=1,
            h2=2,
            h3=3,
            h4=4,
            i1="p1",
            i2="p2",
            i3="p3",
            i4="p4",
            i5="p5",
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
    assert "MTU" not in text
    assert "Jc" not in text
    assert "PersistentKeepalive" not in text
