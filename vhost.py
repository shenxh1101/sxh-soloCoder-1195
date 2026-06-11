#!/usr/bin/env python3
import argparse
import csv
import sys
import os
from pathlib import Path

import registry
import hosts_manager
import config_generators
import ssl_manager


def validate_project_name(project):
    if not project:
        return "项目名为空"
    if not project.isalnum():
        return f"项目名只能包含字母和数字，收到: '{project}'"
    if not project[0].isalpha():
        return f"项目名必须以字母开头，收到: '{project}'"
    return None


def validate_port(port_str):
    try:
        port = int(port_str)
        if port < 1 or port > 65535:
            return f"端口号超出范围: {port_str}"
        return None
    except (ValueError, TypeError):
        return f"端口号无效: '{port_str}'"


def _validate_csv_row(row_num, project, port_str, existing_projects):
    errors = []
    proj_err = validate_project_name(project)
    if proj_err:
        errors.append(proj_err)
    port_err = validate_port(port_str)
    if port_err:
        errors.append(port_err)
    if project and project in existing_projects:
        errors.append(f"项目 '{project}' 已存在")
    return errors


def _resolve_profile(args):
    return getattr(args, "profile", None) or registry.get_current_profile()


def _print_dry_run_banner(command):
    print("=" * 60)
    print(f"  DRY-RUN 模式 - 预览 {command} 操作")
    print("=" * 60)
    print("以下是将要执行的操作，不会实际修改任何文件。\n")


def _print_dry_run_add(project, port, proxy, https, docker, profile):
    preview = config_generators.preview_config(project, port, proxy=proxy, https=https, docker=docker, profile=profile)
    print(f"  分组: {profile}")
    print(f"  [Hosts] 将添加: {preview['hosts_entry']}")
    print(f"  [配置] 将生成: {preview['config_path']}")
    if https:
        print(f"  [SSL]  将生成证书: {preview.get('cert_path', 'N/A')}")
        print(f"  [SSL]  将生成私钥: {preview.get('key_path', 'N/A')}")
    if docker:
        print(f"  [Docker] 将生成: {preview.get('docker_config_path', 'N/A')}")
    print(f"\n提示: 确认无误后，去掉 --dry-run 正式执行。")


def _print_dry_run_delete(project, profile):
    preview = config_generators.preview_delete(project, profile=profile)
    print(f"  分组: {profile}")
    print(f"  [Hosts] 将移除: {preview['hosts_entry']}")
    if preview["files_to_remove"]:
        print(f"  [文件] 将删除以下文件:")
        for f in preview["files_to_remove"]:
            print(f"         - {f}")
    else:
        print(f"  [文件] 没有需要删除的文件")
    print(f"\n提示: 确认无误后，去掉 --dry-run 正式执行。")


def _domain_from_project(project):
    return f"{project}.test"


def _collect_files_for_project(project, profile):
    d = _domain_from_project(project)
    files = []
    configs_dir = registry.get_configs_dir(profile)
    for ext in [".conf", ".Caddyfile"]:
        p = configs_dir / f"{d}{ext}"
        if p.exists():
            files.append(str(p))
    certs_dir = registry.get_certs_dir(profile)
    cert_domain_dir = certs_dir / d
    if cert_domain_dir.exists():
        for f in cert_domain_dir.iterdir():
            files.append(str(f))
    docker_dir = registry.get_docker_configs_dir(profile)
    dp = docker_dir / f"{d}.docker-compose.yml"
    if dp.exists():
        files.append(str(dp))
    return files


def _undo_add(operation):
    profile = operation["profile"]
    hosts_entries = operation.get("hosts_entries", [])
    for entry_str in hosts_entries:
        domain = entry_str.split()[-1] if entry_str else ""
        if domain:
            hosts_manager.remove_hosts_entry(domain)

    for project in operation.get("domains", []):
        for f in _collect_files_for_project(project, profile):
            try:
                Path(f).unlink()
            except Exception:
                pass
        registry.remove_domain(project, profile=profile)

    registry.commit_pop_operation()


def _undo_delete(operation):
    profile = operation["profile"]
    snapshot = operation.get("snapshot", {})

    for project, entry_data in snapshot.items():
        registry.add_domain(
            project, entry_data["port"],
            proxy=entry_data.get("proxy", "nginx"),
            https=entry_data.get("https", False),
            docker=entry_data.get("docker", False),
            profile=profile,
        )

        d = _domain_from_project(project)
        proxy_type = entry_data.get("proxy", "nginx")
        https = entry_data.get("https", False)
        docker = entry_data.get("docker", False)
        port = entry_data["port"]

        key_path = None
        cert_path = None
        if https:
            key_path, cert_path = ssl_manager.generate_self_signed_cert(d, profile=profile)
            if cert_path:
                registry.update_domain(project, profile=profile, cert_path=cert_path)
            else:
                registry.update_domain(project, profile=profile, https=False)

        config_path, docker_config_path = config_generators.generate_config(
            project, port,
            proxy=proxy_type, https=(https and key_path is not None),
            key_path=key_path, cert_path=cert_path, docker=docker, profile=profile,
        )
        registry.update_domain(project, profile=profile, config_path=config_path)
        if docker_config_path:
            registry.update_domain(project, profile=profile, docker_config_path=docker_config_path)

    for entry_str in operation.get("hosts_entries", []):
        domain = entry_str.split()[-1] if entry_str else ""
        if domain:
            hosts_manager.add_hosts_entry(domain)

    registry.commit_pop_operation()


def _undo_import(operation):
    profile = operation["profile"]
    hosts_entries = operation.get("hosts_entries", [])
    for entry_str in hosts_entries:
        domain = entry_str.split()[-1] if entry_str else ""
        if domain:
            hosts_manager.remove_hosts_entry(domain)

    for project in operation.get("domains", []):
        for f in _collect_files_for_project(project, profile):
            try:
                Path(f).unlink()
            except Exception:
                pass
        registry.remove_domain(project, profile=profile)

    registry.commit_pop_operation()


_UNDO_HANDLERS = {
    "add": _undo_add,
    "delete": _undo_delete,
    "import": _undo_import,
}


def cmd_add(args):
    project = args.project
    port = args.port
    profile = _resolve_profile(args)

    proj_err = validate_project_name(project)
    if proj_err:
        print(f"错误: {proj_err}")
        sys.exit(1)

    port_err = validate_port(port)
    if port_err:
        print(f"错误: {port_err}")
        sys.exit(1)

    if args.dry_run:
        _print_dry_run_banner("add")
        _print_dry_run_add(project, int(port), args.proxy, args.https, args.docker, profile)
        return

    hosts_manager.require_admin()

    entry, err = registry.add_domain(
        project, int(port),
        proxy=args.proxy, https=args.https, docker=args.docker, profile=profile,
    )
    if err:
        print(f"错误: {err}")
        sys.exit(1)

    domain = entry["domain"]

    print(f"正在生成虚拟域名配置: {domain} -> localhost:{port}  [分组: {profile}]")

    hosts_entries = []
    success = hosts_manager.add_hosts_entry(domain)
    if success:
        hosts_entries.append(f"127.0.0.1 {domain}")
    else:
        print(f"警告: hosts 条目已存在: {domain}")

    key_path = None
    cert_path = None
    if args.https:
        print("正在生成自签名 SSL 证书...")
        key_path, cert_path = ssl_manager.generate_self_signed_cert(domain, profile=profile)
        if key_path and cert_path:
            print(f"  SSL 证书: {cert_path}")
            print(f"  SSL 私钥: {key_path}")
            registry.update_domain(project, profile=profile, cert_path=cert_path)
        else:
            print("  警告: 未找到 OpenSSL，跳过证书生成。")
            registry.update_domain(project, profile=profile, https=False)
            entry["https"] = False

    config_path, docker_config_path = config_generators.generate_config(
        project, int(port),
        proxy=args.proxy, https=entry.get("https", False),
        key_path=key_path, cert_path=cert_path, docker=args.docker, profile=profile,
    )

    registry.update_domain(project, profile=profile, config_path=config_path)
    if docker_config_path:
        registry.update_domain(project, profile=profile, docker_config_path=docker_config_path)

    files_created = [config_path]
    if docker_config_path:
        files_created.append(docker_config_path)
    if key_path:
        files_created.append(key_path)
    if cert_path:
        files_created.append(cert_path)

    registry.record_operation(
        "add", profile,
        {project: entry}, hosts_entries, files_created,
    )

    print(f"\n虚拟域名 '{domain}' 配置完成!")
    print(f"  Hosts: 127.0.0.1 {domain}")
    print(f"  反向代理配置: {config_path}")

    if args.https and key_path:
        print(f"  HTTPS 已启用")
        print(f"  证书路径: {cert_path}")

    if args.docker and docker_config_path:
        print(f"  Docker Compose Override: {docker_config_path}")
        print(f"  使用方式: docker compose -f docker-compose.yml -f {docker_config_path} up")

    if args.proxy == "nginx":
        print(f"\n使用方法:")
        print(f"  include {config_path};")
        print(f"\n校验配置: vhost test {project}{' --profile ' + profile if profile != 'default' else ''}")
        print(f"重载 nginx: nginx -s reload")
    else:
        print(f"\n使用方法:")
        print(f"  caddy run --config {config_path}")
        print(f"\n校验配置: vhost test {project}{' --profile ' + profile if profile != 'default' else ''}")


def cmd_list(args):
    profile = _resolve_profile(args)
    domains = registry.list_domains(profile=profile)

    print(f"\n当前分组: {profile}")

    all_profiles = registry.list_profiles()
    if len(all_profiles) > 1:
        print(f"可用分组: {', '.join(all_profiles)}")
        print(f"切换分组: vhost profile use <名称>")

    if not domains:
        print("\n当前没有配置任何虚拟域名。")
        print("使用 'vhost add <项目名> <端口>' 来添加。")
        return

    print(f"\n{'项目名':<20} {'域名':<25} {'端口':<8} {'代理':<8} {'HTTPS':<8} {'Docker':<8}")
    print("-" * 80)

    for entry in domains:
        https_status = "✓" if entry.get("https") else "✗"
        docker_status = "✓" if entry.get("docker") else "✗"
        print(
            f"{entry['project']:<20} "
            f"{entry['domain']:<25} "
            f"{entry['port']:<8} "
            f"{entry.get('proxy', 'nginx'):<8} "
            f"{https_status:<8} "
            f"{docker_status:<8}"
        )

    print("-" * 80)
    print(f"共 {len(domains)} 个虚拟域名")

    hosts_entries = hosts_manager.list_vhost_entries()
    if hosts_entries:
        print(f"\nHosts 文件中的 .test/.local 条目:")
        for he in hosts_entries:
            print(f"  {he['ip']}  {he['domain']}")


def cmd_delete(args):
    profile = _resolve_profile(args)

    if args.dry_run:
        entry = registry.get_domain(args.project, profile=profile)
        if not entry:
            print(f"错误: 项目 '{args.project}' 在分组 '{profile}' 中不存在")
            sys.exit(1)
        _print_dry_run_banner("delete")
        _print_dry_run_delete(args.project, profile)
        return

    hosts_manager.require_admin()

    entry, err = registry.remove_domain(args.project, profile=profile)
    if err:
        print(f"错误: {err}")
        sys.exit(1)

    domain = entry["domain"]
    hosts_entries = [f"127.0.0.1 {domain}"]
    hosts_manager.remove_hosts_entry(domain)

    if entry.get("https") or entry.get("cert_path"):
        ssl_manager.remove_cert(domain, profile=profile)

    config_generators.remove_config(domain, profile=profile)

    registry.record_operation(
        "delete", profile,
        {args.project: entry}, hosts_entries, [],
    )

    print(f"虚拟域名 '{domain}' 已删除。")
    print(f"  hosts 条目已移除")
    print(f"  配置文件已删除")
    if entry.get("https"):
        print(f"  证书文件已删除")
    print(f"\n提示: 如需撤销，执行 vhost undo")


def cmd_import(args):
    csv_path = Path(args.file)
    if not csv_path.exists():
        print(f"错误: 文件不存在: {csv_path}")
        sys.exit(1)

    profile = _resolve_profile(args)

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required_cols = {"project", "port"}
        if reader.fieldnames is None or not required_cols.issubset(set(reader.fieldnames)):
            print(f"错误: CSV 文件必须包含 'project' 和 'port' 列")
            print(f"  CSV 格式示例: project,port,https,docker,proxy")
            sys.exit(1)
        rows = list(reader)

    existing_projects = {e["project"] for e in registry.list_domains(profile=profile)}

    valid_rows = []
    invalid_rows = []

    for row_num, row in enumerate(rows, start=2):
        project = row.get("project", "").strip()
        port_str = row.get("port", "").strip()

        if not project and not port_str:
            continue

        errors = _validate_csv_row(row_num, project, port_str, existing_projects)
        if errors:
            invalid_rows.append({"row_num": row_num, "project": project, "errors": errors})
        else:
            https = row.get("https", "").strip().lower() in ("yes", "true", "1")
            docker = row.get("docker", "").strip().lower() in ("yes", "true", "1")
            proxy = row.get("proxy", "").strip().lower()
            if proxy not in ("nginx", "caddy"):
                proxy = "nginx"

            valid_rows.append({
                "row_num": row_num, "project": project, "port": int(port_str),
                "https": https, "docker": docker, "proxy": proxy,
            })
            existing_projects.add(project)

    if invalid_rows:
        print(f"CSV 校验发现 {len(invalid_rows)} 个问题行:")
        print("-" * 60)
        for inv in invalid_rows:
            print(f"  行 {inv['row_num']} ({inv['project'] or '空'}):")
            for e in inv["errors"]:
                print(f"    - {e}")
        print("-" * 60)

    if not valid_rows:
        print("\n没有可导入的有效行。")
        sys.exit(1)

    if args.dry_run:
        print(f"\n共 {len(valid_rows)} 行通过校验，{len(invalid_rows)} 行存在问题。")
        _print_dry_run_banner("import")
        print(f"分组: {profile}")
        print(f"将导入 {len(valid_rows)} 个域名:\n")
        for row in valid_rows:
            preview = config_generators.preview_config(
                row["project"], row["port"],
                proxy=row["proxy"], https=row["https"], docker=row["docker"], profile=profile,
            )
            print(f"  [{row['project']}] {preview['hosts_entry']} -> :{row['port']}")
            print(f"           配置: {preview['config_path']}")
            if row["https"]:
                print(f"           证书: {preview.get('cert_path', 'N/A')}")
            if row["docker"]:
                print(f"           Docker: {preview.get('docker_config_path', 'N/A')}")

        if invalid_rows:
            print(f"\n校验未通过 ({len(invalid_rows)}):")
            for inv in invalid_rows:
                print(f"  ✗ 行 {inv['row_num']} ({inv['project'] or '空'}): {', '.join(inv['errors'])}")

        print(f"\n提示: 确认无误后，去掉 --dry-run 正式执行。")
        return

    if invalid_rows:
        print(f"\n共 {len(valid_rows)} 行通过校验，{len(invalid_rows)} 行存在问题。")
        answer = input("是否继续导入通过校验的行? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("已取消导入。")
            sys.exit(0)

    hosts_manager.require_admin()

    success_rows = []
    fail_rows = []
    all_hosts_entries = []
    all_files_created = []
    all_domains_snapshot = {}

    for row in valid_rows:
        project = row["project"]
        try:
            entry, err = registry.add_domain(
                project, row["port"],
                proxy=row["proxy"], https=row["https"], docker=row["docker"], profile=profile,
            )
            if err:
                fail_rows.append({"project": project, "error": err})
                continue

            domain = entry["domain"]
            hosts_manager.add_hosts_entry(domain)
            all_hosts_entries.append(f"127.0.0.1 {domain}")

            key_path = None
            cert_path = None
            if row["https"]:
                key_path, cert_path = ssl_manager.generate_self_signed_cert(domain, profile=profile)
                if key_path and cert_path:
                    registry.update_domain(project, profile=profile, cert_path=cert_path)
                else:
                    registry.update_domain(project, profile=profile, https=False)

            config_path, docker_config_path = config_generators.generate_config(
                project, row["port"],
                proxy=row["proxy"], https=entry.get("https", False),
                key_path=key_path, cert_path=cert_path, docker=row["docker"], profile=profile,
            )
            registry.update_domain(project, profile=profile, config_path=config_path)
            if docker_config_path:
                registry.update_domain(project, profile=profile, docker_config_path=docker_config_path)

            all_files_created.append(config_path)
            if docker_config_path:
                all_files_created.append(docker_config_path)
            if key_path:
                all_files_created.append(key_path)
            if cert_path:
                all_files_created.append(cert_path)

            all_domains_snapshot[project] = registry.get_domain(project, profile=profile)
            success_rows.append(project)

        except Exception as e:
            fail_rows.append({"project": project, "error": str(e)})

    if all_domains_snapshot:
        registry.record_operation(
            "import", profile, all_domains_snapshot, all_hosts_entries, all_files_created,
        )

    print(f"\n{'='*60}")
    print(f"批量导入完成")
    print(f"{'='*60}")
    print(f"  成功: {len(success_rows)}")
    if success_rows:
        for p in success_rows:
            print(f"    ✓ {p}")
    print(f"  失败: {len(fail_rows)}")
    if fail_rows:
        for f_row in fail_rows:
            print(f"    ✗ {f_row['project']}: {f_row['error']}")
    if invalid_rows:
        print(f"  校验未通过: {len(invalid_rows)}")
        for inv in invalid_rows:
            print(f"    ✗ 行 {inv['row_num']} ({inv['project'] or '空'}): {', '.join(inv['errors'])}")

    if success_rows:
        print(f"\n提示: 如需撤销整批导入，执行 vhost undo")


def cmd_backup(args):
    backup_path = hosts_manager.backup_hosts()
    print(f"Hosts 文件已备份到: {backup_path}")


def cmd_info(args):
    profile = _resolve_profile(args)
    entry = registry.get_domain(args.project, profile=profile)
    if not entry:
        print(f"错误: 项目 '{args.project}' 在分组 '{profile}' 中不存在")
        sys.exit(1)

    print(f"\n项目: {entry['project']}")
    print(f"域名: {entry['domain']}")
    print(f"端口: {entry['port']}")
    print(f"分组: {entry.get('profile', 'default')}")
    print(f"代理: {entry.get('proxy', 'nginx')}")
    print(f"HTTPS: {'是' if entry.get('https') else '否'}")
    print(f"Docker: {'是' if entry.get('docker') else '否'}")
    print(f"创建时间: {entry.get('created_at', 'N/A')}")
    if entry.get("config_path"):
        print(f"配置文件: {entry['config_path']}")
    if entry.get("cert_path"):
        print(f"SSL 证书: {entry['cert_path']}")
    if entry.get("docker") and entry.get("docker_config_path"):
        print(f"Docker 配置: {entry['docker_config_path']}")
        print(f"使用方式: docker compose -f docker-compose.yml -f {entry['docker_config_path']} up")

    if hosts_manager.has_hosts_entry(entry["domain"]):
        print(f"Hosts: ✓ 已配置")
    else:
        print(f"Hosts: ✗ 未配置")


def cmd_doctor(args):
    print("VHost 环境诊断")
    print("=" * 60)

    results = []

    print("\n[1/5] hosts 文件写入权限检查")
    hosts_path = hosts_manager.get_hosts_path()
    if hosts_path.exists():
        writable, msg = hosts_manager.check_write_permission()
        if writable:
            results.append(("hosts 读写", "OK", f"可读写 {hosts_path}"))
        else:
            if "sudo" in msg.lower() or "管理员" in msg:
                results.append(("hosts 读写", "WARN", f"{msg} - 请使用 sudo 或管理员权限运行"))
            else:
                results.append(("hosts 读写", "FAIL", msg))
    else:
        results.append(("hosts 读写", "FAIL", f"hosts 文件不存在: {hosts_path}"))

    print("\n[2/5] OpenSSL 检查")
    if ssl_manager.openssl_available():
        results.append(("OpenSSL", "OK", "已安装可用"))
    else:
        results.append(("OpenSSL", "WARN", "未找到（HTTPS 功能不可用）"))

    print("\n[3/5] 代理配置目录检查")
    profiles = registry.list_profiles()
    for p in profiles:
        configs_dir = registry.get_configs_dir(p)
        conf_count = sum(1 for _ in configs_dir.glob("*.conf")) if configs_dir.exists() else 0
        caddy_count = sum(1 for _ in configs_dir.glob("*.Caddyfile")) if configs_dir.exists() else 0
        results.append(("配置目录", "OK", f"[{p}] {configs_dir} (nginx:{conf_count}, caddy:{caddy_count})"))

        certs_dir = registry.get_certs_dir(p)
        cert_count = sum(1 for _ in certs_dir.rglob("*.crt")) if certs_dir.exists() else 0
        results.append(("证书目录", "OK", f"[{p}] {certs_dir} ({cert_count} 个证书)"))

        docker_dir = registry.get_docker_configs_dir(p)
        docker_count = sum(1 for _ in docker_dir.rglob("*.yml")) if docker_dir.exists() else 0
        results.append(("Docker目录", "OK", f"[{p}] {docker_dir} ({docker_count} 个配置)"))

    print("\n[4/5] 注册表一致性检查")
    all_domains = registry.list_all_domains()
    if all_domains:
        orphan_count = 0
        for entry in all_domains:
            hosts_ok = hosts_manager.has_hosts_entry(entry["domain"])
            config_exists = (
                Path(entry.get("config_path", "")).exists()
                if entry.get("config_path") else False
            )
            if not hosts_ok or not config_exists:
                orphan_count += 1
                status = []
                if not hosts_ok:
                    status.append("hosts缺失")
                if not config_exists:
                    status.append("配置缺失")
                results.append(("域名状态", "WARN",
                    f"[{entry.get('profile', '?')}] {entry['domain']}: {', '.join(status)}"))

        if orphan_count == 0:
            results.append(("注册表", "OK", f"{len(all_domains)} 个域名跨 {len(profiles)} 个分组，状态正常"))
        else:
            results.append(("注册表", "WARN", f"{len(all_domains)} 个域名，{orphan_count} 个异常"))
    else:
        results.append(("注册表", "OK", f"无域名，{len(profiles)} 个分组"))

    print("\n[5/5] 备份与操作历史")
    backup_dir = hosts_manager.get_backup_dir()
    backups = list(backup_dir.glob("hosts_backup_*")) if backup_dir.exists() else []
    results.append(("备份", "OK", f"{len(backups)} 个备份"))

    ops = registry.list_operations()
    results.append(("操作历史", "OK", f"{len(ops)} 条记录"))

    print("\n" + "=" * 60)
    print("诊断结果汇总")
    print("=" * 60)

    status_order = {"FAIL": 0, "WARN": 1, "OK": 2}
    for name, status, detail in sorted(results, key=lambda x: status_order.get(x[1], 99)):
        icon = {"OK": "✓", "WARN": "⚠", "FAIL": "✗"}.get(status, "?")
        print(f"  [{icon} {status}] {name}: {detail}")

    fail_count = sum(1 for _, s, _ in results if s == "FAIL")
    warn_count = sum(1 for _, s, _ in results if s == "WARN")
    ok_count = sum(1 for _, s, _ in results if s == "OK")

    print(f"\n  OK: {ok_count}  警告: {warn_count}  失败: {fail_count}")

    if fail_count > 0:
        print("\n请修复以上失败项后再使用 vhost。")
    elif warn_count > 0:
        print("\n存在警告项，部分功能可能受限。")
    else:
        print("\n环境一切正常，可以正常使用 vhost！")


def cmd_history(args):
    ops = registry.list_operations(args.count)
    if not ops:
        print("暂无操作记录。")
        return

    print(f"\n最近 {len(ops)} 条操作记录:")
    print("-" * 70)
    for op in reversed(ops):
        type_icon = {"add": "+", "delete": "-", "import": "*"}.get(op["type"], "?")
        domains = ", ".join(op.get("domains", []))
        profile = op.get("profile", "default")
        print(f"  [{type_icon}] {op['id']}  {op['type']:<8} [{profile}] {domains}")
        print(f"        {op['timestamp']}")
    print("-" * 70)
    print(f"\n撤销最近一次操作: vhost undo")


def cmd_undo(args):
    op = registry.get_last_operation()
    if not op:
        print("没有可撤销的操作。")
        return

    op_type = op["type"]
    domains = ", ".join(op.get("domains", []))
    profile = op.get("profile", "default")

    print(f"将撤销以下操作:")
    print(f"  类型: {op_type}")
    print(f"  分组: {profile}")
    print(f"  域名: {domains}")
    print(f"  时间: {op['timestamp']}")

    answer = input("\n确认撤销? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        print("已取消。")
        return

    if op_type not in _UNDO_HANDLERS:
        print(f"不支持撤销 '{op_type}' 类型的操作。")
        sys.exit(1)

    hosts_manager.require_admin()

    try:
        _UNDO_HANDLERS[op_type](op)
        print(f"\n✓ 已撤销 {op_type} 操作 ({domains})")
    except Exception as e:
        print(f"\n✗ 撤销失败: {e}")
        sys.exit(1)


def cmd_test(args):
    profile = _resolve_profile(args)
    entry = registry.get_domain(args.project, profile=profile)
    if not entry:
        print(f"错误: 项目 '{args.project}' 在分组 '{profile}' 中不存在")
        sys.exit(1)

    config_path = entry.get("config_path", "")
    if not config_path or not Path(config_path).exists():
        print(f"错误: 配置文件不存在: {config_path}")
        sys.exit(1)

    proxy_type = entry.get("proxy", "nginx")

    print(f"正在校验 {proxy_type} 配置: {config_path}")
    ok, msg, detail = config_generators.test_config(config_path, proxy_type)

    if ok:
        print(f"✓ {msg}")
        if detail:
            print(detail)
        reload_cmd = config_generators.get_reload_command(proxy_type)
        print(f"\n配置校验通过。重载命令:")
        print(f"  {reload_cmd}")
    else:
        print(f"\n✗ 配置校验失败")
        print(f"  配置文件: {config_path}")
        print(f"  校验命令: ", end="")
        if proxy_type == "caddy":
            print(f"caddy validate --config {config_path}")
        else:
            print(f"nginx -t -c <临时 wrapper 配置> (include {config_path})")
        print(f"  失败原因: {msg}")
        if detail:
            print(f"  错误详情:")
            for line in detail.strip().split("\n"):
                if line.strip():
                    print(f"    {line}")
        sys.exit(1)


def cmd_profile(args):
    if args.profile_command == "list":
        profiles = registry.list_profiles()
        current = registry.get_current_profile()
        print(f"\n当前分组: {current}")
        print(f"\n所有分组:")
        for p in profiles:
            marker = " *" if p == current else ""
            count = len(registry.list_domains(profile=p))
            print(f"  {p}{marker} ({count} 个域名)")
        print(f"\n共 {len(profiles)} 个分组")

    elif args.profile_command == "use":
        name = args.name
        registry.set_current_profile(name)
        count = len(registry.list_domains(profile=name))
        print(f"已切换到分组 '{name}' ({count} 个域名)")

    elif args.profile_command == "delete":
        name = args.name
        ok, err = registry.delete_profile(name)
        if not ok:
            print(f"错误: {err}")
            sys.exit(1)
        print(f"分组 '{name}' 已删除。域名数据仍保留在 hosts 和配置文件中。")
        print(f"如需清理，请手动删除 ~/.vhost/configs/{name} 等目录。")

    else:
        print("用法: vhost profile {list|use|delete} [参数]\n")
        print("子命令:")
        print("  list          列出所有分组")
        print("  use  <名称>   切换当前分组")
        print("  delete <名称> 删除分组")
        print("\n示例:")
        print("  vhost profile list")
        print("  vhost profile use work")
        print("  vhost profile delete team-a")
        sys.exit(0 if args.profile_command is None else 1)


def main():
    parser = argparse.ArgumentParser(
        prog="vhost",
        description="虚拟域名管理器 - 本地开发环境虚拟域名配置工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  vhost add myblog 3000                     添加虚拟域名
  vhost add myblog 3000 --dry-run           预览将要执行的操作
  vhost add myblog 3000 --https --docker    完整配置
  vhost add myblog 3000 --profile work      添加到 work 分组
  vhost list                                列出当前分组域名
  vhost list --profile work                 列出 work 分组域名
  vhost info myblog                         查看域名详情
  vhost delete myblog                       删除虚拟域名
  vhost import projects.csv                 批量导入
  vhost import projects.csv --dry-run       预览批量导入
  vhost test myblog                         校验代理配置
  vhost test myblog --profile work          校验 work 分组配置
  vhost history                             查看操作记录
  vhost undo                                撤销最近一次操作
  vhost backup                              手动备份 hosts
  vhost doctor                              环境诊断
  vhost profile list                        列出所有分组
  vhost profile use work                    切换到 work 分组
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    def add_profile_arg(p):
        p.add_argument("--profile", default=None, help="指定分组名称（默认使用当前分组）")

    def add_dry_run_arg(p):
        p.add_argument("--dry-run", action="store_true", help="预览模式，不实际修改文件")

    parser_add = subparsers.add_parser("add", help="添加虚拟域名")
    parser_add.add_argument("project", help="项目名称（将生成 <项目名>.test 域名）")
    parser_add.add_argument("port", help="本地端口号")
    parser_add.add_argument("--proxy", choices=["nginx", "caddy"], default="nginx", help="反向代理类型 (默认: nginx)")
    parser_add.add_argument("--https", action="store_true", help="启用 HTTPS 并生成自签名证书")
    parser_add.add_argument("--docker", action="store_true", help="生成 Docker Compose Override 配置")
    add_profile_arg(parser_add)
    add_dry_run_arg(parser_add)
    parser_add.set_defaults(func=cmd_add)

    parser_list = subparsers.add_parser("list", help="列出所有虚拟域名")
    add_profile_arg(parser_list)
    parser_list.set_defaults(func=cmd_list)

    parser_delete = subparsers.add_parser("delete", help="删除虚拟域名")
    parser_delete.add_argument("project", help="要删除的项目名称")
    add_profile_arg(parser_delete)
    add_dry_run_arg(parser_delete)
    parser_delete.set_defaults(func=cmd_delete)

    parser_import = subparsers.add_parser("import", help="从 CSV 批量导入")
    parser_import.add_argument("file", help="CSV 文件路径")
    add_profile_arg(parser_import)
    add_dry_run_arg(parser_import)
    parser_import.set_defaults(func=cmd_import)

    parser_backup = subparsers.add_parser("backup", help="手动备份 hosts 文件")
    parser_backup.set_defaults(func=cmd_backup)

    parser_info = subparsers.add_parser("info", help="查看虚拟域名详情")
    parser_info.add_argument("project", help="项目名称")
    add_profile_arg(parser_info)
    parser_info.set_defaults(func=cmd_info)

    parser_doctor = subparsers.add_parser("doctor", help="环境诊断检查")
    parser_doctor.set_defaults(func=cmd_doctor)

    parser_history = subparsers.add_parser("history", help="查看操作记录")
    parser_history.add_argument("-n", "--count", type=int, default=10, help="显示条数 (默认: 10)")
    parser_history.set_defaults(func=cmd_history)

    parser_undo = subparsers.add_parser("undo", help="撤销最近一次操作")
    parser_undo.set_defaults(func=cmd_undo)

    parser_test = subparsers.add_parser("test", help="校验代理配置")
    parser_test.add_argument("project", help="项目名称")
    add_profile_arg(parser_test)
    parser_test.set_defaults(func=cmd_test)

    parser_profile = subparsers.add_parser("profile", help="分组管理")
    profile_subs = parser_profile.add_subparsers(dest="profile_command", help="分组操作")
    profile_list = profile_subs.add_parser("list", help="列出所有分组")
    profile_list.set_defaults(func=cmd_profile)
    profile_use = profile_subs.add_parser("use", help="切换当前分组")
    profile_use.add_argument("name", help="分组名称")
    profile_use.set_defaults(func=cmd_profile)
    profile_delete = profile_subs.add_parser("delete", help="删除分组")
    profile_delete.add_argument("name", help="分组名称")
    profile_delete.set_defaults(func=cmd_profile)
    parser_profile.set_defaults(func=cmd_profile)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()