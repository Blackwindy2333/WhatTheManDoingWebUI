#!/usr/bin/env python3
"""WhatTheManDoing WebUI 配置管理命令行。

仅修改 config.json（网页无后台）。所有写入都会经过 server.config 校验，
不合法的配置不会落盘。

用法示例：
  python config_cli.py show
  python config_cli.py set serve.port 9090
  python config_cli.py device list
  python config_cli.py device add --id lap-1 --name Laptop --url http://1.2.3.4:8765/api/v1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    ConfigError,
    WebUIConfig,
    ensure_config,
    load_config,
    save_config,
    validate_config_dict,
)

# 可用「点号路径」读写的标量配置项（devices[] 请用 device 子命令）。
SCALAR_KEYS = {
    "version": int,
    "refresh_interval_seconds": int,
    "page_title": str,
    "page_subtitle": str,
    "show_history": bool,
    "devices_per_page": int,
    "trust_proxy": bool,
    "forwarded_allow_ips": str,
    "serve.mode": str,
    "serve.host": str,
    "serve.port": int,
    "serve.ssl_certfile": (str, type(None)),
    "serve.ssl_keyfile": (str, type(None)),
    "log.level": str,
    "log.dir": str,
    "log.filename": str,
    "log.max_bytes": int,
    "log.backup_count": int,
    "log.console": bool,
    "log.access_log": bool,
}


def _die(msg: str, code: int = 1) -> None:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def _resolve_config_path(path: str | None) -> Path:
    return Path(path).expanduser().resolve() if path else DEFAULT_CONFIG_PATH


def _load(path: Path) -> WebUIConfig:
    if not path.exists():
        _die(f"找不到配置文件：{path}（可执行 config init 创建）")
    try:
        return load_config(path)
    except (ConfigError, json.JSONDecodeError, OSError) as exc:
        _die(f"配置无效 {path}：{exc}")


def _config_to_dict(cfg: WebUIConfig) -> dict[str, Any]:
    return {
        "version": cfg.version,
        "refresh_interval_seconds": cfg.refresh_interval_seconds,
        "page_title": cfg.page_title,
        "page_subtitle": cfg.page_subtitle,
        "show_history": cfg.show_history,
        "devices_per_page": cfg.devices_per_page,
        "trust_proxy": cfg.trust_proxy,
        "forwarded_allow_ips": cfg.forwarded_allow_ips,
        "serve": {
            "mode": cfg.serve.mode,
            "host": cfg.serve.host,
            "port": cfg.serve.port,
            "ssl_certfile": cfg.serve.ssl_certfile,
            "ssl_keyfile": cfg.serve.ssl_keyfile,
        },
        "log": {
            "level": cfg.log.level,
            "dir": cfg.log.dir,
            "filename": cfg.log.filename,
            "max_bytes": cfg.log.max_bytes,
            "backup_count": cfg.log.backup_count,
            "console": cfg.log.console,
            "access_log": cfg.log.access_log,
        },
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "api_base_url": d.api_base_url,
                "api_token": d.api_token,
                "enabled": d.enabled,
            }
            for d in cfg.devices
        ],
    }


def _parse_value(raw: str, expected: type | tuple[type, ...]) -> Any:
    text = raw.strip()
    if expected is bool or (isinstance(expected, tuple) and bool in expected):
        low = text.lower()
        if low in ("true", "1", "yes", "on", "真"):
            return True
        if low in ("false", "0", "no", "off", "假"):
            return False
        _die(f"应为布尔值（true/false），收到：{raw!r}")
    if expected is int or (isinstance(expected, tuple) and int in expected):
        try:
            return int(text)
        except ValueError:
            _die(f"应为整数，收到：{raw!r}")
    if text.lower() in ("null", "none") and (
        expected is type(None) or (isinstance(expected, tuple) and type(None) in expected)
    ):
        return None
    return text


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur: Any = data
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            _die(f"未知配置路径：{dotted}")
        cur = cur[part]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        _die(f"未知配置路径：{dotted}")
    cur[parts[-1]] = value


def _get_dotted(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            _die(f"未知配置路径：{dotted}")
        cur = cur[part]
    return cur


def _save_validated(data: dict[str, Any], path: Path) -> WebUIConfig:
    try:
        cfg = validate_config_dict(data)
    except ConfigError as exc:
        _die(f"校验失败：{exc}")
    save_config(cfg, path)
    return cfg


def cmd_show(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    data = _config_to_dict(cfg)
    if args.key:
        value = _get_dotted(data, args.key)
        if args.raw and isinstance(value, (str, int, bool)) or value is None:
            print(value if value is not None else "null")
        else:
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_path(args: argparse.Namespace) -> None:
    print(_resolve_config_path(args.config))


def cmd_validate(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    if not path.exists():
        _die(f"找不到配置文件：{path}")
    try:
        load_config(path)
    except (ConfigError, json.JSONDecodeError, OSError) as exc:
        _die(f"校验失败：{exc}")
    print(f"校验通过：{path}")


def cmd_init(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    if path.exists() and not args.force:
        _die(f"配置已存在：{path}（如需覆盖请加 --force）")
    example = ROOT / "config.example.json"
    if example.exists() and not args.blank:
        data = json.loads(example.read_text(encoding="utf-8"))
        _save_validated(data, path)
    else:
        ensure_config(path)
    print(f"已创建：{path}")


def cmd_edit(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    if not path.exists():
        if args.create:
            cmd_init(argparse.Namespace(config=args.config, force=False, blank=False))
        else:
            _die(f"找不到配置文件：{path}")
    editor = args.editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        if os.name == "nt":
            editor = "notepad"
        else:
            editor = "vi"
    try:
        subprocess.call([editor, str(path)])
    except OSError as exc:
        _die(f"无法打开编辑器 {editor!r}：{exc}")
    print(f"已编辑：{path}")
    # 手工修改后再校验一次
    try:
        load_config(path)
        print("校验通过：配置有效")
    except ConfigError as exc:
        print(f"警告：编辑后配置无效：{exc}", file=sys.stderr)
        raise SystemExit(2)


def cmd_keys(_args: argparse.Namespace) -> None:
    for key in SCALAR_KEYS:
        print(key)


def cmd_set(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    data = _config_to_dict(cfg)
    if args.key not in SCALAR_KEYS:
        _die(f"未知配置键：{args.key}（可用键见 keys；设备请用 device ...）")
    value = _parse_value(args.value, SCALAR_KEYS[args.key])
    _set_dotted(data, args.key, value)
    _save_validated(data, path)
    print(f"已设置 {args.key} = {json.dumps(value, ensure_ascii=False)}")


def cmd_device_list(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    if not cfg.devices:
        print("（暂无设备）")
        return
    for d in cfg.devices:
        state = "启用" if d.enabled else "停用"
        token = "未设置" if not d.api_token else "已设置"
        print(f"{d.id}\t{d.name}\t{d.api_base_url}\ttoken={token}\t{state}")


def cmd_device_show(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    for d in cfg.devices:
        if d.id == args.id:
            print(
                json.dumps(
                    {
                        "id": d.id,
                        "name": d.name,
                        "api_base_url": d.api_base_url,
                        "api_token": d.api_token,
                        "enabled": d.enabled,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
    _die(f"找不到设备：{args.id}")


def cmd_device_add(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    if any(d.id == args.id for d in cfg.devices):
        _die(f"设备 ID 已存在：{args.id}")
    entry = {
        "id": args.id,
        "name": args.name or args.id,
        "api_base_url": args.api_base_url,
        "api_token": args.api_token or "",
        "enabled": True if args.enabled is None else args.enabled == "true",
    }
    data = _config_to_dict(cfg)
    data["devices"].append(entry)
    _save_validated(data, path)
    print(f"已添加设备：{args.id}")


def cmd_device_set(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    data = _config_to_dict(cfg)
    found = False
    for item in data["devices"]:
        if item["id"] != args.id:
            continue
        found = True
        if args.name is not None:
            item["name"] = args.name
        if args.api_base_url is not None:
            item["api_base_url"] = args.api_base_url
        if args.api_token is not None:
            item["api_token"] = args.api_token
        if args.enabled is not None:
            item["enabled"] = args.enabled == "true"
        if args.rename:
            item["id"] = args.rename
    if not found:
        _die(f"找不到设备：{args.id}")
    _save_validated(data, path)
    print(f"已更新设备：{args.id}")


def cmd_device_remove(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    data = _config_to_dict(cfg)
    before = len(data["devices"])
    data["devices"] = [d for d in data["devices"] if d["id"] != args.id]
    if len(data["devices"]) == before:
        _die(f"找不到设备：{args.id}")
    _save_validated(data, path)
    print(f"已删除设备：{args.id}")


def cmd_backup(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    if not path.exists():
        _die(f"找不到配置文件：{path}")
    if args.dest:
        dest = Path(args.dest).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = path.with_name(f"{path.name}.bak-{stamp}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    print(f"已备份：{dest}")


def cmd_restore(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    src = Path(args.src).expanduser().resolve()
    if not src.exists():
        _die(f"找不到备份文件：{src}")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"无法读取备份：{exc}")
    _save_validated(data, path)
    print(f"已恢复：{path} ← {src}")


def cmd_export(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    text = json.dumps(_config_to_dict(cfg), ensure_ascii=False, indent=2) + "\n"
    if args.out:
        out = Path(args.out).expanduser().resolve()
        out.write_text(text, encoding="utf-8")
        print(f"已导出：{out}")
    else:
        sys.stdout.write(text)


def cmd_interactive(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    print(f"配置文件：{path}")
    if not path.exists():
        print("配置不存在——可选 3 从 config.example.json 创建。")
    print(
        """
1) 查看配置
2) 校验配置
3) 初始化（从示例创建）
4) 用编辑器打开
5) 修改配置项
6) 设备列表
7) 添加设备
8) 删除设备
9) 备份配置
10) 从备份恢复
0) 退出
"""
    )
    while True:
        try:
            choice = input("配置> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("0", "q", "quit", "exit", "退出"):
            return
        if choice == "1":
            cmd_show(argparse.Namespace(config=args.config, key=None, raw=False))
        elif choice == "2":
            cmd_validate(argparse.Namespace(config=args.config))
        elif choice == "3":
            cmd_init(argparse.Namespace(config=args.config, force=False, blank=False))
        elif choice == "4":
            cmd_edit(argparse.Namespace(config=args.config, editor=None, create=True))
        elif choice == "5":
            key = input("配置键（如 serve.port）：").strip()
            value = input("新值：").strip()
            cmd_set(argparse.Namespace(config=args.config, key=key, value=value))
        elif choice == "6":
            cmd_device_list(argparse.Namespace(config=args.config))
        elif choice == "7":
            did = input("设备 ID：").strip()
            name = input("显示名称：").strip()
            url = input("api_base_url：").strip()
            token = input("api_token：").strip()
            cmd_device_add(
                argparse.Namespace(
                    config=args.config,
                    id=did,
                    name=name or did,
                    api_base_url=url,
                    api_token=token,
                    enabled="true",
                )
            )
        elif choice == "8":
            did = input("要删除的设备 ID：").strip()
            cmd_device_remove(argparse.Namespace(config=args.config, id=did))
        elif choice == "9":
            cmd_backup(argparse.Namespace(config=args.config, dest=None))
        elif choice == "10":
            src = input("备份文件路径：").strip()
            cmd_restore(argparse.Namespace(config=args.config, src=src))
        else:
            print("无效选项，请重新输入。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="config_cli",
        description="管理 WhatTheManDoing WebUI 的 config.json（仅配置文件，无网页后台）。",
    )
    parser.add_argument(
        "-c",
        "--config",
        help=f"指定 config.json 路径（默认：{DEFAULT_CONFIG_PATH}）",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("show", help="以 JSON 打印配置")
    p.add_argument("key", nargs="?", help="可选点号路径，如 serve.port")
    p.add_argument("--raw", action="store_true", help="标量原样输出（不带 JSON 引号）")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("path", help="打印配置文件完整路径")
    p.set_defaults(func=cmd_path)

    p = sub.add_parser("validate", help="校验配置文件")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("init", help="从 config.example.json 创建配置")
    p.add_argument("--force", action="store_true", help="覆盖已有文件")
    p.add_argument("--blank", action="store_true", help="使用内置默认值而非示例文件")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("edit", help="用 $EDITOR / 记事本打开配置，关闭后自动校验")
    p.add_argument("--editor", help="指定编辑器命令")
    p.add_argument("--create", action="store_true", help="文件不存在时先创建")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("keys", help="列出可写配置键")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("set", help="修改标量配置项（见 keys）")
    p.add_argument("key", help="点号路径，如 refresh_interval_seconds")
    p.add_argument("value", help="值（布尔 true/false；整数；字符串；null）")
    p.set_defaults(func=cmd_set)

    dev = sub.add_parser("device", help="管理 devices[] 设备列表")
    dev_sub = dev.add_subparsers(dest="device_command", required=True)

    p = dev_sub.add_parser("list", help="列出设备")
    p.set_defaults(func=cmd_device_list)

    p = dev_sub.add_parser("show", help="查看单台设备")
    p.add_argument("id", help="设备 ID")
    p.set_defaults(func=cmd_device_show)

    p = dev_sub.add_parser("add", help="添加设备")
    p.add_argument("--id", required=True, help="设备 ID（与上游 device_id 一致）")
    p.add_argument("--name", help="显示名称（默认同 ID）")
    p.add_argument("--url", dest="api_base_url", required=True, help="API 根地址，如 http://127.0.0.1:8765/api/v1")
    p.add_argument("--token", dest="api_token", help="上游 api_token")
    p.add_argument("--enabled", choices=["true", "false"], help="是否启用")
    p.set_defaults(func=cmd_device_add)

    p = dev_sub.add_parser("set", help="修改设备字段")
    p.add_argument("id", help="原设备 ID")
    p.add_argument("--rename", help="改为新的设备 ID")
    p.add_argument("--name", help="显示名称")
    p.add_argument("--url", dest="api_base_url", help="API 根地址")
    p.add_argument("--token", dest="api_token", help="上游 api_token")
    p.add_argument("--enabled", choices=["true", "false"], help="是否启用")
    p.set_defaults(func=cmd_device_set)

    p = dev_sub.add_parser("remove", help="删除设备")
    p.add_argument("id", help="设备 ID")
    p.set_defaults(func=cmd_device_remove)

    p = sub.add_parser("backup", help="备份配置为带时间戳的副本")
    p.add_argument("dest", nargs="?", help="备份文件路径（可选）")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="从备份文件恢复配置")
    p.add_argument("src", help="备份文件路径")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("export", help="导出配置 JSON 到文件或标准输出")
    p.add_argument("--out", help="输出文件（默认打印到终端）")
    p.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # 无子命令 → 进入交互菜单（config.bat / config.sh 无参数时也走这里）
        args.config = getattr(args, "config", None)
        cmd_interactive(args)
        return
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        raise SystemExit(1)
    func(args)


if __name__ == "__main__":
    main()
