import pytest

from agent import dns as dns_module
from agent.dns import apply_dns
from shared.schemas import DnsRule


@pytest.fixture
def fake_runner(monkeypatch):
    calls: list[list[str]] = []

    async def fake_run(command, *, stdin=None, check=True):
        calls.append(list(command))
        return ""

    monkeypatch.setattr(dns_module, "run_command", fake_run)
    return calls


async def test_render_config_emits_dual_family_ipsets():
    rules = [
        DnsRule(name="streaming", domains=["netflix.com", "*.nflxvideo.net"], ipset_name="streaming"),
        DnsRule(name="ai", domains=["claude.ai"], ipset_name="ai"),
    ]
    config = dns_module._render_config(rules=rules)
    # dnsmasq формат: ipset=/domain/v4set,v6set — A-записи в v4, AAAA в v6.
    assert "ipset=/netflix.com/nflxvideo.net/streaming-v4,streaming-v6" in config
    assert "ipset=/claude.ai/ai-v4,ai-v6" in config


async def test_apply_dns_creates_dual_ipsets_writes_and_reloads(tmp_path, fake_runner):
    config_path = tmp_path / "waygate.conf"
    rules = [DnsRule(name="ai", domains=["claude.ai"], ipset_name="ai")]
    response = await apply_dns(rules=rules, config_path=config_path)
    assert response.applied == 1
    assert response.errors == []
    assert "ipset=/claude.ai/ai-v4,ai-v6" in config_path.read_text()
    # Перед reload созданы оба ipset'а.
    create_calls = [call for call in fake_runner if call[:3] == ["ipset", "create", "-exist"]]
    set_names = {call[3] for call in create_calls}
    assert set_names == {"ai-v4", "ai-v6"}
    families = {tuple(call[5:7]) for call in create_calls}
    assert families == {("family", "inet"), ("family", "inet6")}
    assert any("systemctl" in part and "reload" in call for call in fake_runner for part in call)


async def test_apply_dns_idempotent_skips_reload(tmp_path, fake_runner):
    """Повторный apply с тем же config'ом — не reload'ит dnsmasq, но всё равно
    делает `ipset create -exist` (это idempotent и нужно если ipset был удалён)."""
    config_path = tmp_path / "waygate.conf"
    rules = [DnsRule(name="ai", domains=["claude.ai"], ipset_name="ai")]
    await apply_dns(rules=rules, config_path=config_path)
    fake_runner.clear()
    response = await apply_dns(rules=rules, config_path=config_path)
    assert response.applied == 0
    # Ipset-create вызван (это норма — `-exist` делает no-op если уже есть).
    # Reload систему НЕ дёргали.
    assert all("systemctl" not in part for call in fake_runner for part in call)


async def test_apply_dns_reports_reload_error(tmp_path, monkeypatch):
    """Если systemctl reload падает — это в errors, но applied всё равно >0
    (конфиг записан). ipset create тоже логирует ошибки если bin не найден."""
    from agent.subprocess_runner import CommandError

    async def fail(command, *, stdin=None, check=True):
        raise CommandError(command=list(command), returncode=1, stderr="not found")

    monkeypatch.setattr(dns_module, "run_command", fail)
    config_path = tmp_path / "waygate.conf"
    response = await apply_dns(
        rules=[DnsRule(name="ai", domains=["claude.ai"], ipset_name="ai")],
        config_path=config_path,
    )
    assert response.applied == 1
    assert any("reload dnsmasq" in err for err in response.errors)
