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


def cmd_add(args):
    hosts_manager.require_admin()

    project = args.project
    port = args.port

    if not project.isalnum():
        print(f"错误: 项目名只能包含字母和数字，收到: '{project}'")
        sys.exit(1)

    try:
        port = int(port)
        if port < 1 or port > 65535:
            raise ValueError
    except ValueError:
        print(f"错误: 端口号无效: '{args.port}'，必须是 1-65535 之间的整数")
        sys.exit(1)

    entry, err = registry.add_domain(
        project, port,
        proxy=args.proxy,
        https=args.https,
        docker=args.docker,
    )
    if err:
        print(f"错误: {err}")
        sys.exit(1)

    domain = entry["domain"]

    print(f"正在生成虚拟域名配置: {project}.test -> localhost:{port}")

    success = hosts_manager.add_hosts_entry(domain)
    if not success:
        print(f"警告: hosts 条目已存在: {domain}")

    key_path = None
    cert_path = None
    if args.https:
        print("正在生成自签名 SSL 证书...")
        key_path, cert_path = ssl_manager.generate_self_signed_cert(domain)
        if key_path and cert_path:
            print(f"  SSL 证书: {cert_path}")
            print(f"  SSL 私钥: {key_path}")
            registry.update_domain(project, cert_path=cert_path)
        else:
            print("  警告: 未找到 OpenSSL，跳过证书生成。请安装 OpenSSL 或手动生成。")
            registry.update_domain(project, https=False)
            entry["https"] = False

    config_path, docker_config_path = config_generators.generate_config(
        project, port,
        proxy=args.proxy,
        https=entry.get("https", False),
        key_path=key_path,
        cert_path=cert_path,
    )

    registry.update_domain(project, config_path=config_path)

    if docker_config_path:
        registry.update_domain(project, docker_config_path=docker_config_path)

    print(f"\n虚拟域名 '{domain}' 配置完成!")
    print(f"  Hosts: 127.0.0.1 {domain}")
    print(f"  反向代理配置: {config_path}")

    if args.https and key_path:
        print(f"  HTTPS 已启用")
        print(f"  证书路径: {cert_path}")

    if args.docker:
        print(f"  Docker Compose Override: {docker_config_path}")

    if args.proxy == "nginx":
        print(f"\n使用方法:")
        print(f"  nginx 配置已生成，将其 include 到您的 nginx.conf 中:")
        print(f"  include {config_path};")
    else:
        print(f"\n使用方法:")
        print(f"  caddy run --config {config_path}")


def cmd_list(args):
    domains = registry.list_domains()
    hosts_entries = hosts_manager.list_vhost_entries()

    if not domains:
        print("当前没有配置任何虚拟域名。")
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

    print(f"\nHosts 文件中的 .test/.local 条目:")
    for he in hosts_entries:
        print(f"  {he['ip']}  {he['domain']}")


def cmd_delete(args):
    hosts_manager.require_admin()

    project = args.project
    entry, err = registry.remove_domain(project)
    if err:
        print(f"错误: {err}")
        sys.exit(1)

    domain = entry["domain"]

    hosts_manager.remove_hosts_entry(domain)

    if entry.get("https") or entry.get("cert_path"):
        ssl_manager.remove_cert(domain)

    config_generators.remove_config(domain)

    print(f"虚拟域名 '{domain}' 已删除。")
    print(f"  hosts 条目已移除")
    print(f"  配置文件已删除")
    if entry.get("https"):
        print(f"  证书文件已删除")


def cmd_import(args):
    hosts_manager.require_admin()

    csv_path = Path(args.file)
    if not csv_path.exists():
        print(f"错误: 文件不存在: {csv_path}")
        sys.exit(1)

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        required_cols = {"project", "port"}
        if reader.fieldnames is None or not required_cols.issubset(set(reader.fieldnames)):
            print(f"错误: CSV 文件必须包含 'project' 和 'port' 列")
            print(f"  当前列: {reader.fieldnames}")
            sys.exit(1)

        success_count = 0
        skip_count = 0
        error_count = 0

        for row_num, row in enumerate(reader, start=2):
            project = row.get("project", "").strip()
            port_str = row.get("port", "").strip()

            if not project or not port_str:
                print(f"  行 {row_num}: 跳过空行")
                skip_count += 1
                continue

            try:
                port = int(port_str)
                if port < 1 or port > 65535:
                    raise ValueError
            except ValueError:
                print(f"  行 {row_num} ({project}): 端口无效 '{port_str}'")
                error_count += 1
                continue

            https = row.get("https", "").strip().lower() in ("yes", "true", "1")
            docker = row.get("docker", "").strip().lower() in ("yes", "true", "1")
            proxy = row.get("proxy", "").strip().lower()
            if proxy not in ("nginx", "caddy"):
                proxy = "nginx"

            entry, err = registry.add_domain(project, port, proxy=proxy, https=https, docker=docker)
            if err:
                print(f"  行 {row_num} ({project}): {err}")
                skip_count += 1
                continue

            domain = entry["domain"]
            hosts_manager.add_hosts_entry(domain)

            key_path = None
            cert_path = None
            if https:
                key_path, cert_path = ssl_manager.generate_self_signed_cert(domain)
                if key_path:
                    registry.update_domain(project, cert_path=cert_path)
                else:
                    registry.update_domain(project, https=False)

            config_path, docker_config_path = config_generators.generate_config(
                project, port, proxy=proxy, https=entry.get("https", False),
                key_path=key_path, cert_path=cert_path,
            )
            registry.update_domain(project, config_path=config_path)
            if docker_config_path:
                registry.update_domain(project, docker_config_path=docker_config_path)

            print(f"  ✓ {project}.test -> localhost:{port}")
            success_count += 1

        print(f"\n批量导入完成: 成功 {success_count}, 跳过 {skip_count}, 错误 {error_count}")


def cmd_backup(args):
    backup_path = hosts_manager.backup_hosts()
    print(f"Hosts 文件已备份到: {backup_path}")


def cmd_info(args):
    project = args.project
    entry = registry.get_domain(project)
    if not entry:
        print(f"错误: 项目 '{project}' 不存在")
        sys.exit(1)

    print(f"\n项目: {entry['project']}")
    print(f"域名: {entry['domain']}")
    print(f"端口: {entry['port']}")
    print(f"代理: {entry.get('proxy', 'nginx')}")
    print(f"HTTPS: {'是' if entry.get('https') else '否'}")
    print(f"Docker: {'是' if entry.get('docker') else '否'}")
    print(f"创建时间: {entry.get('created_at', 'N/A')}")
    if entry.get("config_path"):
        print(f"配置文件: {entry['config_path']}")
    if entry.get("cert_path"):
        print(f"SSL 证书: {entry['cert_path']}")
    if entry.get("docker_config_path"):
        print(f"Docker 配置: {entry['docker_config_path']}")

    if hosts_manager.has_hosts_entry(entry["domain"]):
        print(f"Hosts: ✓ 已配置")
    else:
        print(f"Hosts: ✗ 未配置")


def main():
    parser = argparse.ArgumentParser(
        prog="vhost",
        description="虚拟域名管理器 - 本地开发环境虚拟域名配置工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  vhost add myblog 3000                    添加虚拟域名
  vhost add myblog 3000 --https            添加并启用 HTTPS
  vhost add myblog 3000 --proxy caddy      使用 Caddy 代理
  vhost add myblog 3000 --https --docker   完整配置
  vhost list                               列出所有虚拟域名
  vhost info myblog                        查看域名详情
  vhost delete myblog                      删除虚拟域名
  vhost import projects.csv                批量导入
  vhost backup                             手动备份 hosts
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    parser_add = subparsers.add_parser("add", help="添加虚拟域名")
    parser_add.add_argument("project", help="项目名称（将生成 <项目名>.test 域名）")
    parser_add.add_argument("port", help="本地端口号")
    parser_add.add_argument(
        "--proxy", choices=["nginx", "caddy"], default="nginx",
        help="反向代理类型 (默认: nginx)"
    )
    parser_add.add_argument(
        "--https", action="store_true",
        help="启用 HTTPS 并生成自签名证书"
    )
    parser_add.add_argument(
        "--docker", action="store_true",
        help="生成 Docker Compose Override 配置"
    )
    parser_add.set_defaults(func=cmd_add)

    parser_list = subparsers.add_parser("list", help="列出所有虚拟域名")
    parser_list.set_defaults(func=cmd_list)

    parser_delete = subparsers.add_parser("delete", help="删除虚拟域名")
    parser_delete.add_argument("project", help="要删除的项目名称")
    parser_delete.set_defaults(func=cmd_delete)

    parser_import = subparsers.add_parser("import", help="从 CSV 批量导入")
    parser_import.add_argument("file", help="CSV 文件路径")
    parser_import.set_defaults(func=cmd_import)

    parser_backup = subparsers.add_parser("backup", help="手动备份 hosts 文件")
    parser_backup.set_defaults(func=cmd_backup)

    parser_info = subparsers.add_parser("info", help="查看虚拟域名详情")
    parser_info.add_argument("project", help="项目名称")
    parser_info.set_defaults(func=cmd_info)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()