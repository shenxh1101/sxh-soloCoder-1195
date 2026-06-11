import os
import sys
import shutil
import tempfile
import ctypes
from datetime import datetime
from pathlib import Path


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return os.geteuid() == 0


def require_admin():
    if not is_admin():
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
        sys.exit(0)


def get_hosts_path():
    if sys.platform == "win32":
        return Path("C:/Windows/System32/drivers/etc/hosts")
    return Path("/etc/hosts")


def get_backup_dir():
    home = Path.home()
    backup_dir = home / ".vhost" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_hosts():
    hosts_path = get_hosts_path()
    backup_dir = get_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"hosts_backup_{timestamp}"
    shutil.copy2(hosts_path, backup_path)
    return backup_path


def read_hosts():
    hosts_path = get_hosts_path()
    if not hosts_path.exists():
        return []
    with open(hosts_path, "r", encoding="utf-8") as f:
        return f.readlines()


def write_hosts(lines):
    hosts_path = get_hosts_path()
    backup_hosts()
    content = "".join(lines)
    with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    shutil.move(tmp_path, hosts_path)


def add_hosts_entry(domain):
    lines = read_hosts()
    entry = f"127.0.0.1 {domain}\n"

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[1] == domain:
            return False

    lines.append(entry)
    write_hosts(lines)
    return True


def remove_hosts_entry(domain):
    lines = read_hosts()
    new_lines = []
    removed = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[1] == domain:
            removed = True
            continue
        new_lines.append(line)

    if removed:
        write_hosts(new_lines)
    return removed


def has_hosts_entry(domain):
    lines = read_hosts()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and parts[1] == domain:
            return True
    return False


def list_vhost_entries():
    lines = read_hosts()
    entries = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and (parts[1].endswith(".test") or parts[1].endswith(".local")):
            entries.append({"ip": parts[0], "domain": parts[1]})
    return entries