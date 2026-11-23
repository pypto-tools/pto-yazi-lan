# pto-yazi-lan

在 A 和 B 上安装同一个程序，就能从任意一台发起复制。左栏始终是当前登录的机器，右栏是选中的对端。在哪一栏选文件或目录，就从哪一栏按 `F5` 复制到另一栏的当前目录。

## 两台机器各安装一次

在两台服务器各自的源码目录中执行以下命令，安装 `yazi-lan`、Yazi 插件和快捷键配置。

```bash
python3 -m venv ~/.local/share/yazi-lan/venv
source ~/.local/share/yazi-lan/venv/bin/activate
python -m pip install .
```

也可以只在一台机器上运行 `python -m pip wheel --no-deps --wheel-dir dist .`，将生成的 `dist/yazi_lan-0.2.0-py3-none-any.whl` 复制到对端用户的主目录。在对端创建并激活虚拟环境后，运行 `python -m pip install ~/yazi_lan-0.2.0-py3-none-any.whl`。

两台机器都需要 Linux、Python 3.9 及以上、OpenSSH 客户端、iproute2，以及对方可访问的 SSH/SFTP 服务。两端装有 rsync 3.1 及以上时会使用 rsync。独立双栏面板可直接运行，从 Yazi 按 `F5` 打开需要本机装有兼容的 Yazi（已验证 26.9.1）。

## 两边分别配置对方

下面以 A 的内网 IP 为 `10.20.0.11`、B 的内网 IP 为 `10.20.0.22` 为例。用 `ip -br addr` 查看各自的机房地址，用 `ip route get <对端内网IP>` 确认从本机到对方的实际出口。示例中 A 的出口是 `eno1`，B 的出口是 `ens192`，请换成实际值。

| 登录位置 | 本机 `~/.ssh/config` 中的目标 | 远端账户的授权 | 从本机验证 |
| --- | --- | --- | --- |
| A | `server-b` 指向 `10.20.0.22` | B 的目标账户接受 A 用户的公钥 | `sftp server-b` |
| B | `server-a` 指向 `10.20.0.11` | A 的目标账户接受 B 用户的公钥 | `sftp server-a` |

例如，在 A 的 SSH 配置里添加下列条目。在 B 的 SSH 配置里将两处 `server-b` 换成 `server-a`，并填入 A 的地址。省略 `User` 时使用当前用户名，也可以指定对端授权的账户。两边各自保管自己的私钥。首次登录从对端控制台或管理员处核对主机指纹，成功进入 SFTP 后输入 `bye`。

```sshconfig
Host server-b
    HostName 10.20.0.22
    HostKeyAlias server-b
```

在每台机器的 `~/.config/yazi-lan/hosts.json` 写入它要连接的对端。设置了 `XDG_CONFIG_HOME` 时，配置位于其下的 `yazi-lan/hosts.json`。省略 `remote_dir` 时右栏打开远端登录目录。

**A 上的 hosts.json**

```json
{"server-b": {"lan": "10.20.0.22", "interface": "eno1"}}
```

**B 上的 hosts.json**

```json
{"server-a": {"lan": "10.20.0.11", "interface": "ens192"}}
```

## 在任意一端发起复制

在 A 或 B 上都可以运行相同的命令。只配置一个对端时自动选中，配置多个时可在终端按编号选择，也可以直接运行 `yazi-lan server-b` 或 `yazi-lan server-a`。

```bash
yazi-lan --check
yazi-lan
```

也可以直接用命令行指定线路，例如在 B 上运行 `yazi-lan server-a --lan 10.20.0.11 --interface ens192`。

例如在 B 上启动，左栏勾选 `checkpoints/`，右栏进入 A 的 `/data/incoming`。从左栏按 `F5` 和回车，目标是 A 上的 `/data/incoming/checkpoints/`。从右栏发起则会拉取到 B。另一台只需保持 SSH/SFTP 可达，传输面板可以关闭。

要从 Yazi 打开，在任意一端激活上述虚拟环境后用包内的配置启动，选好本机文件再按 `F5`。

```bash
YAZI_CONFIG_HOME="$VIRTUAL_ENV/share/yazi-lan/yazi" yazi .
```

要接入已有的 Yazi 配置，将包内 `share/yazi-lan/yazi/plugins/lan-copy.yazi` 放入自己的 Yazi 插件目录，并将以下条目加入 `keymap.toml`。两边可用同一条快捷键。原有的 `plugin lan-copy <对端>` 和 `YAZI_LAN_PEER` 也能继续使用，显式参数优先。插件通过 `PATH` 查找 `yazi-lan`，也可以把 `YAZI_LAN_BIN` 设为其绝对路径。

```toml
[[mgr.prepend_keymap]]
on = "<F5>"
run = "plugin lan-copy"
desc = "在本机和对端之间复制"
```

## 传输面板

按 `F5` 并确认后，双栏会暂时切换到当前终端的传输输出。rsync 的进度行显示百分比和传输速度，SFTP 的进度行显示当前文件已传和总字节数。传完按回车返回双栏。

| 按键 | 操作 |
| --- | --- |
| `Tab` | 切换本机和对端两栏 |
| `↑`、`↓` 或 `k`、`j` | 移动光标 |
| `Enter`、`l` / `Backspace`、`h` | 进入目录 / 返回上级 |
| 空格 | 勾选或取消当前项，可跨目录选择 |
| `F5` 或 `c` | 把选中项复制到另一栏当前目录，未勾选时使用光标所在项 |
| 在复制提示中按 `Enter` 或 `s` | 合并目录，跳过目标已有的同名文件 |
| 在复制提示中按 `u` | 合并目录，允许更新同名文件，目标独有的文件会保留 |
| 传输时按 `Ctrl-C` | 中断并保留选择，回到面板后用 `F5` 重试 |
| `r` / `q` | 重新选路并连接 / 退出传输面板 |

## 内网和 NetBird

程序检查发起端的路由出口，将 SSH 绑定到该网卡的源地址，再验证对端的主机密钥。内网和 NetBird 入口共用同一份 SSH 主机身份。SFTP 浏览与 rsync 传输复用这次连接，对端的地址由用户显式提供。

要允许回退，在对端配置中增加 `"netbird": "对端的 NetBird IP"` 或在命令行添加 `--netbird IP`。仅当机房路由不符或 SSH 端口网络不可达时才回退。主机密钥不匹配或认证失败会显示错误。通过 NetBird 连接后，面板读取本机 `netbird status --json`，显示直连、中转或类型未知。已配回退时也可以用 `--only-lan` 为本次任务限定线路。

## 目录和中断

默认用 rsync 递归传输，保留文件的权限、修改时间及符号链接。重试时 rsync 可以利用目标端 `.rsync-partial` 内的数据。首版在高速内网上直接传目录，省去生成临时归档的读写开销。

当对端账户只允许 SFTP，或任一端缺少 rsync 时，面板会显示 `SFTP`。此模式逐文件复制，用同目录临时文件在完整接收后发布。中断后可用跳过同名文件的方式重试，当前未完成的文件从头传输。远端覆盖已有文件需要 SFTP 服务支持 OpenSSH 的 POSIX rename 扩展。
