import os
import subprocess
import sys
from pathlib import Path
from registry import get_certs_dir


def openssl_available():
    try:
        subprocess.run(
            ["openssl", "version"],
            capture_output=True,
            check=True,
            shell=(sys.platform == "win32"),
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def generate_self_signed_cert(domain):
    certs_dir = get_certs_dir()
    domain_dir = certs_dir / domain
    domain_dir.mkdir(parents=True, exist_ok=True)

    key_path = domain_dir / f"{domain}.key"
    cert_path = domain_dir / f"{domain}.crt"

    if key_path.exists() and cert_path.exists():
        return str(key_path), str(cert_path)

    if not openssl_available():
        return None, None

    try:
        subprocess.run(
            [
                "openssl", "req", "-x509", "-nodes", "-days", "3650",
                "-newkey", "rsa:2048",
                "-keyout", str(key_path),
                "-out", str(cert_path),
                "-subj", f"/CN={domain}/O=VHost Local Dev/C=CN",
                "-addext", f"subjectAltName=DNS:{domain},DNS:*.{domain}",
            ],
            capture_output=True,
            check=True,
            shell=(sys.platform == "win32"),
        )
        return str(key_path), str(cert_path)
    except subprocess.CalledProcessError as e:
        print(f"生成证书失败: {e.stderr.decode() if e.stderr else str(e)}")
        return None, None


def remove_cert(domain):
    certs_dir = get_certs_dir()
    domain_dir = certs_dir / domain
    if domain_dir.exists():
        import shutil
        shutil.rmtree(domain_dir)