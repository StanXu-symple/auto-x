from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HEALTHCHECKS = {
    "auth-center": "http://127.0.0.1:9100/health/ready",
    "monitor-center": "http://127.0.0.1:9102/health/live",
    "monitor-agent": "http://127.0.0.1:9101/health/live",
}
APPLICATION_SERVICES = {
    "backend": ("xsentinel-backend", "BACKEND_HOST_PORT", "8200"),
    "worker": ("xsentinel-worker", "WORKER_HOST_PORT", "8201"),
    "ai-worker": ("xsentinel-ai-worker", "AI_WORKER_HOST_PORT", "8202"),
    "qq-worker": ("xsentinel-qq-worker", "QQ_WORKER_HOST_PORT", "8203"),
}


def _expected_test(url: str) -> list[str]:
    return [
        "CMD",
        "python",
        "-c",
        f"import urllib.request; urllib.request.urlopen('{url}', timeout=3)",
    ]


def _service_block(source: str, service: str) -> str:
    lines = source.splitlines()
    start = lines.index(f"  {service}:") + 1
    block: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def test_control_plane_services_override_backend_image_healthcheck() -> None:
    source = (REPOSITORY_ROOT / "docker-compose.microservices.yml").read_text()

    for service, url in HEALTHCHECKS.items():
        service_block = _service_block(source, service)
        assert url in service_block
        assert "127.0.0.1:8200" not in service_block


def test_auth_center_waits_for_alembic_and_never_creates_schema() -> None:
    base = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    base_block = _service_block(base, "auth-center")
    microservices = (REPOSITORY_ROOT / "docker-compose.microservices.yml").read_text()
    microservices_block = _service_block(microservices, "auth-center")

    assert 'AUTO_CREATE_TABLES: "false"' in base_block
    assert 'AUTO_CREATE_TABLES: "false"' in microservices_block
    assert "migrate:" in base_block
    assert "condition: service_completed_successfully" in base_block
    assert "migrate:" in microservices_block
    assert "condition: service_completed_successfully" in microservices_block


def test_application_services_have_stable_nacos_identity_and_ports() -> None:
    source = (REPOSITORY_ROOT / "docker-compose.yml").read_text()

    for service, (nacos_name, host_port_variable, port) in APPLICATION_SERVICES.items():
        service_block = _service_block(source, service)
        assert f"NACOS_SERVICE_NAME: {nacos_name}" in service_block
        assert f'NACOS_SERVICE_PORT: "${{{host_port_variable}:-{port}}}"' in service_block
        assert f'- "{port}"' in service_block


def test_control_plane_secrets_are_mounted_by_least_privilege() -> None:
    source = (REPOSITORY_ROOT / "docker-compose.microservices.yml").read_text()
    blocks = {
        service: _service_block(source, service)
        for service in (
            "backend",
            "xhs-worker",
            "auth-center",
            "monitor-center",
            "monitor-agent",
        )
    }

    assert "/backend.secret:" in blocks["backend"]
    assert "private.pem" not in blocks["backend"]
    assert "clients.json" not in blocks["backend"]
    assert "private.pem" not in blocks["xhs-worker"]
    assert "clients.json" not in blocks["xhs-worker"]
    assert "/private.pem:" in blocks["auth-center"]
    assert "/private.pem:/run/xsentinel/private.pem:rw" in blocks["auth-center"]
    assert "/clients.json:" in blocks["auth-center"]
    assert "backend.secret" not in blocks["auth-center"]
    assert "/monitor.secret:" in blocks["monitor-center"]
    assert "private.pem" not in blocks["monitor-center"]
    assert "/services.json:" in blocks["monitor-agent"]
    assert ".secret:" not in blocks["monitor-agent"]


def test_control_plane_nacos_ports_follow_host_port_overrides() -> None:
    source = (REPOSITORY_ROOT / "docker-compose.microservices.yml").read_text()
    expected = {
        "xhs-worker": "${XHS_WORKER_HOST_PORT:-8006}",
        "auth-center": "${AUTH_CENTER_PORT:-9100}",
        "monitor-center": "${MONITOR_CENTER_PORT:-9102}",
        "monitor-agent": "${MONITOR_AGENT_PORT:-9101}",
    }

    for service, advertised_port in expected.items():
        assert f"NACOS_SERVICE_PORT: {advertised_port}" in _service_block(source, service)
    assert "XHS_SERVICE_ADVERTISE_PORT: ${XHS_WORKER_HOST_PORT:-8006}" in _service_block(
        source, "xhs-worker"
    )
    base = (REPOSITORY_ROOT / "docker-compose.yml").read_text()
    assert "XHS_WORKER_HOST_PORT" not in _service_block(base, "xhs-worker")


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is not installed")
def test_compose_config_resolves_control_plane_healthchecks() -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose_files = [
        "docker-compose.yml",
        "docker-compose.auth-center.yml",
        "docker-compose.monitor-center.yml",
        "docker-compose.monitor-agent.yml",
    ]
    command = [
        docker,
        "compose",
        "--project-directory",
        str(REPOSITORY_ROOT),
        "--env-file",
        str(REPOSITORY_ROOT / ".env.example"),
    ]
    for compose_file in compose_files:
        command.extend(["-f", str(REPOSITORY_ROOT / compose_file)])
    command.extend(["config", "--format", "json"])
    environment = os.environ.copy()
    environment["NACOS_SERVER_ADDR"] = "http://nacos:8848"

    result = subprocess.run(command, capture_output=True, text=True, env=environment, check=False)

    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for service, url in HEALTHCHECKS.items():
        assert services[service]["healthcheck"]["test"] == _expected_test(url)
