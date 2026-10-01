#!/usr/bin/env python3
"""Config management CLI for WhatTheManDoing WebUI.

Edits config.json only (there is no admin web UI). All writes go through
server.config validation so invalid files never get saved.

Usage examples:
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
    DeviceConfig,
    WebUIConfig,
    ensure_config,
    load_config,
    save_config,
    validate_config_dict,
)

# Settings that can be read/written with dotted paths (devices[] is separate).
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
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _resolve_config_path(path: str | None) -> Path:
    return Path(path).expanduser().resolve() if path else DEFAULT_CONFIG_PATH


def _load(path: Path) -> WebUIConfig:
    if not path.exists():
        _die(f"config not found: {path} (run: config init)")
    try:
        return load_config(path)
    except (ConfigError, json.JSONDecodeError, OSError) as exc:
        _die(f"invalid config {path}: {exc}")


def _config_to_dict(cfg: WebUIConfig) -> dict[str, Any]:
    data = {
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
    return data


def _parse_value(raw: str, expected: type | tuple[type, ...]) -> Any:
    text = raw.strip()
    if expected is bool or (isinstance(expected, tuple) and bool in expected):
        low = text.lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        _die(f"expected bool, got {raw!r}")
    if expected is int or (isinstance(expected, tuple) and int in expected):
        try:
            return int(text)
        except ValueError:
            _die(f"expected int, got {raw!r}")
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
            _die(f"unknown config path: {dotted}")
        cur = cur[part]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        _die(f"unknown config path: {dotted}")
    cur[parts[-1]] = value


def _get_dotted(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            _die(f"unknown config path: {dotted}")
        cur = cur[part]
    return cur


def _save_validated(data: dict[str, Any], path: Path) -> WebUIConfig:
    try:
        cfg = validate_config_dict(data)
    except ConfigError as exc:
        _die(f"validation failed: {exc}")
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
        _die(f"config not found: {path}")
    try:
        load_config(path)
    except (ConfigError, json.JSONDecodeError, OSError) as exc:
        _die(f"invalid: {exc}")
    print(f"ok: {path}")


def cmd_init(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    if path.exists() and not args.force:
        _die(f"already exists: {path} (use --force to overwrite)")
    example = ROOT / "config.example.json"
    if example.exists() and not args.blank:
        data = json.loads(example.read_text(encoding="utf-8"))
        _save_validated(data, path)
    else:
        ensure_config(path)
    print(f"created: {path}")


def cmd_edit(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    if not path.exists():
        if args.create:
            cmd_init(argparse.Namespace(config=args.config, force=False, blank=False))
        else:
            _die(f"config not found: {path}")
    editor = args.editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        if os.name == "nt":
            editor = "notepad"
        else:
            editor = "vi"
    try:
        subprocess.call([editor, str(path)])
    except OSError as exc:
        _die(f"cannot open editor {editor!r}: {exc}")
    print(f"edited: {path}")
    # Validate after manual edit
    try:
        load_config(path)
        print("ok: config is valid")
    except ConfigError as exc:
        print(f"warning: config invalid after edit: {exc}", file=sys.stderr)
        raise SystemExit(2)


def cmd_keys(_args: argparse.Namespace) -> None:
    for key in SCALAR_KEYS:
        print(key)


def cmd_set(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    data = _config_to_dict(cfg)
    if args.key not in SCALAR_KEYS:
        _die(f"unknown key: {args.key} (see: keys; devices use: device ...)")
    value = _parse_value(args.value, SCALAR_KEYS[args.key])
    _set_dotted(data, args.key, value)
    _save_validated(data, path)
    print(f"set {args.key} = {json.dumps(value, ensure_ascii=False)}")


def _device_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--id")
    p.add_argument("--name")
    p.add_argument("--url", dest="api_base_url", help="API base URL, e.g. http://127.0.0.1:8765/api/v1")
    p.add_argument("--token", dest="api_token", help="upstream api_token")
    p.add_argument("--enabled", choices=["true", "false"])


def cmd_device_list(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    if not cfg.devices:
        print("(no devices)")
        return
    for d in cfg.devices:
        state = "on" if d.enabled else "off"
        token = "(empty)" if not d.api_token else "(set)"
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
    _die(f"device not found: {args.id}")


def cmd_device_add(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    if any(d.id == args.id for d in cfg.devices):
        _die(f"device id already exists: {args.id}")
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
    print(f"added device: {args.id}")


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
        _die(f"device not found: {args.id}")
    _save_validated(data, path)
    print(f"updated device: {args.id}")


def cmd_device_remove(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    data = _config_to_dict(cfg)
    before = len(data["devices"])
    data["devices"] = [d for d in data["devices"] if d["id"] != args.id]
    if len(data["devices"]) == before:
        _die(f"device not found: {args.id}")
    _save_validated(data, path)
    print(f"removed device: {args.id}")


def cmd_backup(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    if not path.exists():
        _die(f"config not found: {path}")
    if args.dest:
        dest = Path(args.dest).expanduser().resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = path.with_name(f"{path.name}.bak-{stamp}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    print(f"backup: {dest}")


def cmd_restore(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    src = Path(args.src).expanduser().resolve()
    if not src.exists():
        _die(f"backup not found: {src}")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _die(f"cannot read backup: {exc}")
    _save_validated(data, path)
    print(f"restored: {path} <- {src}")


def cmd_export(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    cfg = _load(path)
    text = json.dumps(_config_to_dict(cfg), ensure_ascii=False, indent=2) + "\n"
    if args.out:
        out = Path(args.out).expanduser().resolve()
        out.write_text(text, encoding="utf-8")
        print(f"exported: {out}")
    else:
        sys.stdout.write(text)


def cmd_interactive(args: argparse.Namespace) -> None:
    path = _resolve_config_path(args.config)
    print(f"config: {path}")
    if not path.exists():
        print("config missing — choose init to create from config.example.json")
    print(
        """
1) show config
2) validate
3) init (create from example)
4) edit in editor
5) set a key
6) device list
7) device add
8) device remove
9) backup
10) restore
0) quit
"""
    )
    while True:
        try:
            choice = input("config> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("0", "q", "quit", "exit"):
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
            key = input("key (e.g. serve.port): ").strip()
            value = input("value: ").strip()
            cmd_set(argparse.Namespace(config=args.config, key=key, value=value))
        elif choice == "6":
            cmd_device_list(argparse.Namespace(config=args.config))
        elif choice == "7":
            did = input("id: ").strip()
            name = input("name: ").strip()
            url = input("api_base_url: ").strip()
            token = input("api_token: ").strip()
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
            did = input("id to remove: ").strip()
            cmd_device_remove(argparse.Namespace(config=args.config, id=did))
        elif choice == "9":
            cmd_backup(argparse.Namespace(config=args.config, dest=None))
        elif choice == "10":
            src = input("backup path: ").strip()
            cmd_restore(argparse.Namespace(config=args.config, src=src))
        else:
            print("unknown choice")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="config_cli",
        description="Manage WhatTheManDoing WebUI config.json (file-only configuration).",
    )
    parser.add_argument(
        "-c",
        "--config",
        help=f"path to config.json (default: {DEFAULT_CONFIG_PATH})",
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("show", help="print config as JSON")
    p.add_argument("key", nargs="?", help="optional dotted key, e.g. serve.port")
    p.add_argument("--raw", action="store_true", help="print scalar without JSON quoting")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("path", help="print resolved config path")
    p.set_defaults(func=cmd_path)

    p = sub.add_parser("validate", help="validate config file")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("init", help="create config from config.example.json")
    p.add_argument("--force", action="store_true", help="overwrite existing file")
    p.add_argument("--blank", action="store_true", help="create built-in defaults instead of example")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("edit", help="open config in $EDITOR / notepad, then validate")
    p.add_argument("--editor", help="editor command")
    p.add_argument("--create", action="store_true", help="create file first if missing")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("keys", help="list writable setting keys")
    p.set_defaults(func=cmd_keys)

    p = sub.add_parser("set", help="set a scalar setting (see keys)")
    p.add_argument("key", help="dotted key, e.g. refresh_interval_seconds")
    p.add_argument("value", help="value (bool: true/false; int; string; null)")
    p.set_defaults(func=cmd_set)

    dev = sub.add_parser("device", help="manage devices[]")
    dev_sub = dev.add_subparsers(dest="device_command", required=True)

    p = dev_sub.add_parser("list", help="list devices")
    p.set_defaults(func=cmd_device_list)

    p = dev_sub.add_parser("show", help="show one device")
    p.add_argument("id")
    p.set_defaults(func=cmd_device_show)

    p = dev_sub.add_parser("add", help="add device")
    p.add_argument("--id", required=True)
    p.add_argument("--name")
    p.add_argument("--url", dest="api_base_url", required=True)
    p.add_argument("--token", dest="api_token")
    p.add_argument("--enabled", choices=["true", "false"])
    p.set_defaults(func=cmd_device_add)

    p = dev_sub.add_parser("set", help="update device fields")
    p.add_argument("id")
    p.add_argument("--rename", help="new device id")
    p.add_argument("--name")
    p.add_argument("--url", dest="api_base_url")
    p.add_argument("--token", dest="api_token")
    p.add_argument("--enabled", choices=["true", "false"])
    p.set_defaults(func=cmd_device_set)

    p = dev_sub.add_parser("remove", help="remove device")
    p.add_argument("id")
    p.set_defaults(func=cmd_device_remove)

    p = sub.add_parser("backup", help="copy config to a timestamped backup")
    p.add_argument("dest", nargs="?", help="backup file path")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="restore config from a backup file")
    p.add_argument("src", help="backup file path")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("export", help="export config JSON to file or stdout")
    p.add_argument("--out", help="output file (default stdout)")
    p.set_defaults(func=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # No subcommand → interactive menu (also used by config.bat / config.sh)
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
