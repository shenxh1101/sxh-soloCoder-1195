import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path


def get_vhost_dir():
    home = Path.home()
    vhost_dir = home / ".vhost"
    vhost_dir.mkdir(parents=True, exist_ok=True)
    return vhost_dir


def get_registry_path():
    return get_vhost_dir() / "registry.json"


def get_history_path():
    return get_vhost_dir() / "history.json"


def _load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_registry(data):
    if data.get("version") == 2:
        return data
    domains = data.get("domains", {})
    data = {
        "version": 2,
        "current_profile": "default",
        "profiles": {
            "default": {"domains": domains}
        }
    }
    save_registry(data)
    return data


def load_registry():
    data = _load_json(get_registry_path(), {"version": 2, "current_profile": "default", "profiles": {}})
    return _migrate_registry(data)


def save_registry(registry):
    _save_json(get_registry_path(), registry)


def get_current_profile():
    registry = load_registry()
    return registry.get("current_profile", "default")


def set_current_profile(name):
    registry = load_registry()
    if name not in registry.get("profiles", {}):
        registry["profiles"][name] = {"domains": {}}
    registry["current_profile"] = name
    save_registry(registry)
    return name


def list_profiles():
    registry = load_registry()
    return list(registry.get("profiles", {}).keys())


def delete_profile(name):
    registry = load_registry()
    if name == "default":
        return False, "不能删除 default 分组"
    if name not in registry.get("profiles", {}):
        return False, f"分组 '{name}' 不存在"
    if registry["current_profile"] == name:
        registry["current_profile"] = "default"
    del registry["profiles"][name]
    save_registry(registry)
    return True, None


def rename_profile(old_name, new_name):
    if old_name == "default":
        return False, "不能重命名 default 分组"
    if new_name == old_name:
        return False, "新旧名称相同"
    if not _is_valid_profile_name(new_name):
        return False, "分组名只能包含字母、数字、连字符和下划线，且必须以字母开头"

    registry = load_registry()
    if old_name not in registry.get("profiles", {}):
        return False, f"分组 '{old_name}' 不存在"
    if new_name in registry.get("profiles", {}):
        return False, f"分组 '{new_name}' 已存在"

    registry["profiles"][new_name] = registry["profiles"].pop(old_name)
    for entry in registry["profiles"][new_name].get("domains", {}).values():
        entry["profile"] = new_name

    if registry["current_profile"] == old_name:
        registry["current_profile"] = new_name

    save_registry(registry)

    vhost_dir = get_vhost_dir()
    for sub in ["configs", "certs", "docker"]:
        old_dir = vhost_dir / sub / old_name
        new_dir = vhost_dir / sub / new_name
        if old_dir.exists() and not new_dir.exists():
            shutil.move(str(old_dir), str(new_dir))

    return True, None


def copy_profile(src_name, dst_name):
    if not src_name or not dst_name:
        return False, "源分组和目标分组名不能为空"
    if not _is_valid_profile_name(dst_name):
        return False, "目标分组名只能包含字母、数字、连字符和下划线，且必须以字母开头"

    registry = load_registry()
    if src_name not in registry.get("profiles", {}):
        return False, f"分组 '{src_name}' 不存在"
    if dst_name in registry.get("profiles", {}):
        return False, f"分组 '{dst_name}' 已存在"

    import copy
    src_data = copy.deepcopy(registry["profiles"][src_name])
    registry["profiles"][dst_name] = src_data
    for entry in registry["profiles"][dst_name].get("domains", {}).values():
        entry["profile"] = dst_name

    save_registry(registry)

    vhost_dir = get_vhost_dir()
    for sub in ["configs", "certs", "docker"]:
        src_dir = vhost_dir / sub / src_name
        dst_dir = vhost_dir / sub / dst_name
        if src_dir.exists() and not dst_dir.exists():
            shutil.copytree(str(src_dir), str(dst_dir))

    return True, None


def _is_valid_profile_name(name):
    if not name:
        return False
    if not name[0].isalpha():
        return False
    return all(c.isalnum() or c in ('-', '_') for c in name)


def _ensure_profile(registry, profile_name):
    if profile_name is None:
        profile_name = registry.get("current_profile", "default")
    if profile_name not in registry["profiles"]:
        registry["profiles"][profile_name] = {"domains": {}}
    return profile_name


def _get_domains(registry, profile_name=None):
    profile_name = _ensure_profile(registry, profile_name)
    return registry["profiles"][profile_name]["domains"]


def add_domain(project, port, proxy="nginx", https=False, docker=False, profile=None):
    registry = load_registry()
    profile_name = _ensure_profile(registry, profile)
    domains = _get_domains(registry, profile_name)
    domain = f"{project}.test"

    if project in domains:
        return None, f"项目 '{project}' 在分组 '{profile_name}' 中已存在"

    entry = {
        "project": project,
        "port": port,
        "domain": domain,
        "proxy": proxy,
        "https": https,
        "docker": docker,
        "profile": profile_name,
        "created_at": datetime.now().isoformat(),
        "config_path": "",
        "cert_path": "",
        "docker_config_path": "",
    }

    domains[project] = entry
    save_registry(registry)
    return entry, None


def remove_domain(project, profile=None):
    registry = load_registry()
    profile_name = _ensure_profile(registry, profile)
    domains = _get_domains(registry, profile_name)

    if project not in domains:
        return None, f"项目 '{project}' 在分组 '{profile_name}' 中不存在"

    entry = domains.pop(project)
    save_registry(registry)
    return entry, None


def get_domain(project, profile=None):
    registry = load_registry()
    profile_name = _ensure_profile(registry, profile)
    domains = _get_domains(registry, profile_name)
    return domains.get(project)


def list_domains(profile=None):
    registry = load_registry()
    profile_name = _ensure_profile(registry, profile)
    return list(_get_domains(registry, profile_name).values())


def list_all_domains():
    registry = load_registry()
    result = []
    for pname, pdata in registry.get("profiles", {}).items():
        for entry in pdata.get("domains", {}).values():
            result.append(entry)
    return result


def update_domain(project, profile=None, **kwargs):
    registry = load_registry()
    profile_name = _ensure_profile(registry, profile)
    domains = _get_domains(registry, profile_name)

    if project not in domains:
        return None, f"项目 '{project}' 在分组 '{profile_name}' 中不存在"

    domains[project].update(kwargs)
    save_registry(registry)
    return domains[project], None


def get_configs_dir(profile=None):
    if profile is None:
        profile = get_current_profile()
    configs_dir = get_vhost_dir() / "configs" / profile
    configs_dir.mkdir(parents=True, exist_ok=True)
    return configs_dir


def get_certs_dir(profile=None):
    if profile is None:
        profile = get_current_profile()
    certs_dir = get_vhost_dir() / "certs" / profile
    certs_dir.mkdir(parents=True, exist_ok=True)
    return certs_dir


def get_docker_configs_dir(profile=None):
    if profile is None:
        profile = get_current_profile()
    docker_dir = get_vhost_dir() / "docker" / profile
    docker_dir.mkdir(parents=True, exist_ok=True)
    return docker_dir


def load_history():
    return _load_json(get_history_path(), {"operations": []})


def save_history(history):
    _save_json(get_history_path(), history)


def record_operation(op_type, profile, domains_data, hosts_entries, files_created):
    history = load_history()
    if len(history["operations"]) >= 50:
        history["operations"] = history["operations"][-49:]

    op = {
        "id": uuid.uuid4().hex[:12],
        "type": op_type,
        "timestamp": datetime.now().isoformat(),
        "profile": profile,
        "domains": list(domains_data.keys()),
        "snapshot": {k: dict(v) for k, v in domains_data.items()},
        "hosts_entries": list(hosts_entries),
        "files_created": list(files_created),
    }
    history["operations"].append(op)
    save_history(history)
    return op["id"]


def get_last_operation():
    history = load_history()
    ops = history.get("operations", [])
    if not ops:
        return None
    return ops[-1]


def list_operations(count=10):
    history = load_history()
    ops = history.get("operations", [])
    return ops[-count:]


def pop_last_operation():
    history = load_history()
    if not history.get("operations"):
        return None
    return history["operations"].pop()


def commit_pop_operation():
    history = load_history()
    if not history.get("operations"):
        return
    history["operations"].pop()
    save_history(history)


def get_operation(op_id):
    history = load_history()
    for op in history.get("operations", []):
        if op["id"] == op_id:
            return op
    return None