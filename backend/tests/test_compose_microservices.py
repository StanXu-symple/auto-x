from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HEALTHCHECKS = {
    "auth-center": "http://127.0.0.1:9100/health/live",
    "monitor-center": "http://127.0.0.1:9102/health/live",
    "monitor-agent": "http://127.0.0.1:9101/health/live",
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
        assert "127.0.0.1:8000" not in service_block


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
