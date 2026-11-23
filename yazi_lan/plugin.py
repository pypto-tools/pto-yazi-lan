"""Enable the installed Yazi plugin for the invoking user."""

import os
import sys
from pathlib import Path


def enable_plugin():
    prefix = Path(sys.prefix)
    current = prefix.parent.parent / "current"
    if prefix.parent.name == "versions" and current.resolve() == prefix.resolve():
        prefix = current
    shipped = prefix / "share/yazi-lan/yazi"
    conf = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    config = Path(os.getenv("YAZI_CONFIG_HOME") or conf / "yazi")
    plugin = Path("plugins/lan-copy.yazi")
    if not (shipped / plugin / "main.lua").is_file():
        raise ValueError(f"安装中缺少 Yazi 插件 {shipped / plugin}")
    (config / plugin.parent).mkdir(parents=True, exist_ok=True)
    target = config / plugin
    try:
        target.symlink_to(shipped / plugin)
    except FileExistsError:
        if not target.is_symlink() or target.readlink() != shipped / plugin:
            print(f"已有插件 {target}，可从 {shipped / plugin} 手动合并。")
            return
    keymap = config / "keymap.toml"
    default_keys = (shipped / "keymap.toml").read_bytes()
    try:
        with keymap.open("xb") as file:
            file.write(default_keys)
    except FileExistsError:
        if keymap.is_file() and keymap.read_bytes() == default_keys:
            return
        print(f"已保留 {keymap}。如需 F5，可合并 {shipped / 'keymap.toml'} 中的条目。")
