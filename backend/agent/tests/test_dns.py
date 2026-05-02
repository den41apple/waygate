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


async def test_render_config_groups_domains_per_ipset():
    rules = [
        DnsRule(name="streaming", domains=["netflix.com", "*.nflxvideo.net"], ipset_name="streaming"),
        DnsRule(name="ai", domains=["claude.ai"], ipset_name="ai"),
    ]
    config = dns_module._render_config(rules=rules)
    assert "ipset=/netflix.com/nflxvideo.net/streaming" in config
    assert "ipset=/claude.ai/ai" in config


async def test_apply_dns_writes_and_reloads(tmp_path, fake_runner):
    config_path = tmp_path / "waygate.conf"
    rules = [DnsRule(name="ai", domains=["claude.ai"], ipset_name="ai")]
    response = await apply_dns(rules=rules, config_path=config_path)
    assert response.applied == 1
    assert response.errors == []
    assert "ipset=/claude.ai/ai" in config_path.read_text()
    assert any("systemctl" in part and "reload" in call for call in fake_runner for part in call)


async def test_apply_dns_idempotent_skips_reload(tmp_path, fake_runner):
    config_path = tmp_path / "waygate.conf"
    rules = [DnsRule(name="ai", domains=["claude.ai"], ipset_name="ai")]
    await apply_dns(rules=rules, config_path=config_path)
    fake_runner.clear()
    response = await apply_dns(rules=rules, config_path=config_path)
    assert response.applied == 0
    assert fake_runner == []


async def test_apply_dns_reports_reload_error(tmp_path, monkeypatch):
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
    assert response.errors and "reload dnsmasq" in response.errors[0]
