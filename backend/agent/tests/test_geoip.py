from agent.geoip import _build_restore_input, _parse_zone_file


def test_parse_zone_file_skips_comments_and_blank_lines() -> None:
    text = """# header comment
# another
1.2.3.0/24

  4.5.6.0/24
# trailing
7.8.9.0/24
"""
    assert _parse_zone_file(text) == ["1.2.3.0/24", "4.5.6.0/24", "7.8.9.0/24"]


def test_build_restore_input_is_idempotent() -> None:
    """Регрессия: до фикса restore падал с `Set cannot be created: set with the
    same name already exists`, если destroy предыдущего _new-сета не сработал
    (например, на нём ещё висит ссылка из iptables). `-exist` + `flush` делают
    операцию повторно-применимой."""
    payload = _build_restore_input(set_name="waygate-ru-v4_new", cidrs=["1.2.3.0/24", "4.5.0.0/16"])
    text = payload.decode("utf-8")

    lines = text.strip().split("\n")
    assert lines[0].endswith("-exist"), "create должен быть с -exist чтобы не падать на дубликате"
    assert lines[1] == "flush waygate-ru-v4_new", "flush гарантирует пустой сет перед add"
    assert lines[2] == "add waygate-ru-v4_new 1.2.3.0/24"
    assert lines[3] == "add waygate-ru-v4_new 4.5.0.0/16"


def test_build_restore_input_handles_empty_cidrs() -> None:
    payload = _build_restore_input(set_name="empty-set", cidrs=[])
    text = payload.decode("utf-8")

    lines = text.strip().split("\n")
    # Только create+flush, без add — пустой сет тоже валиден.
    assert len(lines) == 2
    assert lines[0].startswith("create empty-set")
    assert lines[1] == "flush empty-set"
