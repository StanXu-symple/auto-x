#!/usr/bin/env python3
"""Explicitly migrate a monitor node in Nacos, preserving operator settings."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import ipaddress
import json
import os
import re
from pathlib import Path


def helper():
    spec = importlib.util.spec_from_file_location(
        "nacos_config", Path(__file__).with_name("nacos-config.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate_documents(documents: dict, old: str, new: str, address: str) -> dict:
    if old == new or not all(re.fullmatch(r"[a-zA-Z0-9_-]+", item) for item in (old, new)):
        raise RuntimeError("旧、新节点名必须不同，且只能包含字母、数字、下划线或连字符")
    ipaddress.ip_address(address)
    result = copy.deepcopy(documents)
    h = helper()
    topology = result["topology"]
    h.validate_monitor_topology(topology)
    nodes = topology["nodes"]
    if old in nodes:
        if new in nodes:
            raise RuntimeError(f"目标节点 {new} 已在拓扑中，拒绝覆盖")
        node = nodes.pop(old)
        if node.get("agent_service_name") == f"xsentinel-monitor-agent-{old}":
            node["agent_service_name"] = f"xsentinel-monitor-agent-{new}"
        elif node.get("agent_service_name") not in (None, ""):
            raise RuntimeError("旧节点使用自定义 agent 服务名，请先核对部署身份")
        nodes[new] = node
        prefix, replacement = old.replace("-", "") + "-", new.replace("-", "") + "-"
        for service in topology["services"]:
            if service["node"] != old:
                continue
            service["node"] = new
            if service["id"].startswith(prefix):
                service["id"] = replacement + service["id"][len(prefix):]
            if service["name"] == f"Monitor Agent {old}":
                service["name"] = f"Monitor Agent {new}"
    elif new not in nodes:
        raise RuntimeError("拓扑中旧节点和目标节点均不存在")
    h.validate_monitor_topology(topology)
    addresses = result["nodes"].get("nodes")
    if not isinstance(addresses, dict):
        raise RuntimeError("节点地址文档缺少 nodes 对象")
    if any(name not in {old, new} and value.get("advertise_ip") == address
           for name, value in addresses.items() if isinstance(value, dict)):
        raise RuntimeError("目标地址已被其他节点使用")
    if old in addresses:
        if new in addresses:
            raise RuntimeError("目标节点已存在地址映射，拒绝覆盖")
        entry = addresses.pop(old)
        entry["advertise_ip"] = address
        addresses[new] = entry
    elif new not in addresses or addresses[new].get("advertise_ip") != address:
        raise RuntimeError("节点地址不符合本次迁移，拒绝覆盖")
    config = result["config"]
    key = "SERVICE_AUTH_CLIENTS_JSON"
    clients = config.get(key)
    was_string = isinstance(clients, str)
    if was_string:
        clients = json.loads(clients)
    if not isinstance(clients, dict) or not isinstance(clients.get("monitor"), dict):
        raise RuntimeError("共享配置缺少 monitor 客户端授权")
    grants = clients["monitor"].get("grants")
    if not isinstance(grants, dict):
        raise RuntimeError("monitor 客户端 grants 格式错误")
    source, target = f"agent:{old}", f"agent:{new}"
    if source in grants:
        if target in grants and grants[target] != grants[source]:
            raise RuntimeError("新旧监控授权不一致，拒绝覆盖")
        grants[target] = grants.pop(source)
    elif target not in grants:
        raise RuntimeError("新旧节点均没有监控授权")
    config[key] = json.dumps(clients, ensure_ascii=False) if was_string else clients
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--from-node", required=True)
    parser.add_argument("--to-node", required=True)
    parser.add_argument("--advertise-ip", required=True)
    parser.add_argument("--apply", action="store_true", help="default: validate only")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    h = helper()
    env = h.parse_env(args.env_file, include_excluded=True)
    server = str(env["NACOS_SERVER_ADDR"]).rstrip("/")
    if server.endswith("/nacos"):
        server = server[:-6]
    common = dict(namespace=env.get("NACOS_NAMESPACE") or "public",
                  group=env.get("NACOS_CONFIG_GROUP") or env.get("NACOS_GROUP") or "X_SENTINEL",
                  timeout=h.positive_timeout(env.get("NACOS_CONFIG_TIMEOUT_SECONDS")))
    ids = dict(config=env.get("NACOS_CONFIG_DATA_ID") or "x-sentinel-config.json",
               topology=env.get("NACOS_MONITOR_TOPOLOGY_DATA_ID") or "x-sentinel-monitor-topology.json",
               nodes=env.get("NACOS_MONITOR_NODES_DATA_ID") or "x-sentinel-monitor-nodes.json")
    _, token = h.load_remote(server, data_id=ids["config"],
                             username=env.get("NACOS_USERNAME", ""),
                             password=env.get("NACOS_PASSWORD", ""), **common)
    common["token"] = token
    before = {key: h.load_json_document(server, data_id=data_id, **common)
              for key, data_id in ids.items()}
    if any(value is None for value in before.values()):
        raise RuntimeError("迁移要求三个 Nacos Data ID 均已存在")
    after = migrate_documents(before, args.from_node, args.to_node, args.advertise_ip)
    changed = [key for key in ids if before[key] != after[key]]
    print(f"节点迁移校验成功: {args.from_node} → {args.to_node}，待写入 {len(changed)} 个 Data ID")
    if not args.apply or not changed:
        return
    if not args.backup_dir:
        raise RuntimeError("--apply 必须指定新的 --backup-dir")
    args.backup_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    for key, data_id in ids.items():
        path = args.backup_dir / data_id
        with path.open("x", encoding="utf-8") as f:
            os.chmod(path, 0o600)
            json.dump(before[key], f, ensure_ascii=False, indent=2)
    for key, data_id in ids.items():
        if h.load_json_document(server, data_id=data_id, **common) != before[key]:
            raise RuntimeError("备份期间 Nacos 配置发生变化，请重新校验")
    # Nacos has no transaction across Data IDs. All inputs are validated first;
    # backups and per-document idempotence allow recovery after partial writes.
    for key in changed:
        h.publish(server, data_id=ids[key], content=after[key], **common)
        if h.load_json_document(server, data_id=ids[key], **common) != after[key]:
            raise RuntimeError(f"{ids[key]} 写入后校验失败，请保留备份并报告")
        print(f"已迁移并核对: {ids[key]}")
    print("Nacos 迁移完成；既有 PostgreSQL 监控授权需另执行 migrate-monitor-grant.sql")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        raise SystemExit(f"错误: {exc}") from exc
