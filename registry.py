import json
import os
from datetime import datetime
from pathlib import Path


def get_registry_path():
    home = Path.home()
    registry_dir = home / ".vhost"
    registry_dir.mkdir(parents=True, exist_ok=True)
    return registry_dir / "registry.json"


def load_registry():
    path = get_registry_path()
    if not path.exists():
        return {"version": 1, "domains": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(registry):
    path = get_registry_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def add_domain(project, port, proxy="nginx", https=False, docker=False):
    registry = load_registry()
    domain = f"{project}.test"

    if project in registry["domains"]:
        return None, f"项目 '{project}' 已存在"

    entry = {
        "project": project,
        "port": port,
        "domain": domain,
        "proxy": proxy,
        "https": https,
        "docker": docker,
        "created_at": datetime.now().isoformat(),
        "config_path": "",
        "cert_path": "",
        "docker_config_path": "",
    }

    registry["domains"][project] = entry
    save_registry(registry)
    return entry, None


def remove_domain(project):
    registry = load_registry()
    if project not in registry["domains"]:
        return None, f"项目 '{project}' 不存在"

    entry = registry["domains"].pop(project)
    save_registry(registry)
    return entry, None


def get_domain(project):
    registry = load_registry()
    return registry["domains"].get(project)


def list_domains():
    registry = load_registry()
    return list(registry["domains"].values())


def update_domain(project, **kwargs):
    registry = load_registry()
    if project not in registry["domains"]:
        return None, f"项目 '{project}' 不存在"

    registry["domains"][project].update(kwargs)
    save_registry(registry)
    return registry["domains"][project], None


def get_configs_dir():
    home = Path.home()
    configs_dir = home / ".vhost" / "configs"
    configs_dir.mkdir(parents=True, exist_ok=True)
    return configs_dir


def get_certs_dir():
    home = Path.home()
    certs_dir = home / ".vhost" / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)
    return certs_dir


def get_docker_configs_dir():
    home = Path.home()
    docker_dir = home / ".vhost" / "docker"
    docker_dir.mkdir(parents=True, exist_ok=True)
    return docker_dir