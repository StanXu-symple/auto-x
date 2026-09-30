import copy
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "migration", Path(__file__).parents[1] / "migrate-monitor-node.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def documents():
    topology = json.loads(
        (Path(__file__).parents[2] / "microservices" / "services.tc-dual.json").read_text()
    )
    topology = json.loads(json.dumps(topology).replace("hn-1", "tc-1").replace("hn1-", "tc1-"))
    topology["interval_seconds"] = 21
    topology["nodes"]["tc-1"]["custom"] = "keep"
    topology["services"][0]["port"] = 18006
    return dict(topology=topology, nodes={"custom": True, "nodes": {
        "tc-1": {"advertise_ip": "203.0.113.1", "custom": "keep"},
        "tc-2": {"advertise_ip": "203.0.113.2"},
    }}, config={"custom": {"keep": True}, "SERVICE_AUTH_CLIENTS_JSON": json.dumps({
        "monitor": {"secret_sha256": "unchanged", "grants": {
            "agent:tc-1": "resources:read", "agent:tc-2": "resources:read",
        }}, "backend": {"grants": {"monitor": "monitor:read"}},
    })})


def run(documents):
    return migration.migrate_documents(documents, "tc-1", "hn-1", "203.0.113.3")


def test_preserves_custom_fields_other_nodes_and_credentials(documents):
    before = copy.deepcopy(documents)
    after = run(documents)
    assert documents == before
    assert set(after["topology"]["nodes"]) == {"hn-1", "tc-2"}
    assert after["topology"]["interval_seconds"] == 21
    assert after["topology"]["nodes"]["hn-1"]["custom"] == "keep"
    assert after["topology"]["services"][0]["port"] == 18006
    assert after["topology"]["services"][4:] == before["topology"]["services"][4:]
    assert after["nodes"]["nodes"]["hn-1"] == {"advertise_ip": "203.0.113.3", "custom": "keep"}
    assert after["nodes"]["nodes"]["tc-2"] == before["nodes"]["nodes"]["tc-2"]
    clients = json.loads(after["config"]["SERVICE_AUTH_CLIENTS_JSON"])
    assert clients["monitor"]["secret_sha256"] == "unchanged"
    assert clients["monitor"]["grants"] == {
        "agent:hn-1": "resources:read", "agent:tc-2": "resources:read"
    }
    assert after["config"]["custom"] == before["config"]["custom"]
    assert run(after) == after


@pytest.mark.parametrize("completed", [("config",), ("config", "topology")])
def test_can_recover_after_partial_publication(documents, completed):
    expected = run(documents)
    for key in completed:
        documents[key] = expected[key]
    assert run(documents) == expected


@pytest.mark.parametrize("kind", ["topology", "address", "id", "grant"])
def test_rejects_collisions_without_mutating_input(documents, kind):
    if kind == "topology":
        documents["topology"]["nodes"]["hn-1"] = {}
    elif kind == "address":
        documents["nodes"]["nodes"]["tc-2"]["advertise_ip"] = "203.0.113.3"
    elif kind == "id":
        documents["topology"]["services"][4]["id"] = "hn1-xhs-worker"
    else:
        clients = json.loads(documents["config"]["SERVICE_AUTH_CLIENTS_JSON"])
        clients["monitor"]["grants"]["agent:hn-1"] = "different"
        documents["config"]["SERVICE_AUTH_CLIENTS_JSON"] = json.dumps(clients)
    before = copy.deepcopy(documents)
    with pytest.raises(RuntimeError):
        run(documents)
    assert documents == before
