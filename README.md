# pto-yazi-lan

在 A 和 B 上安装同一个程序，就能从任意一台发起复制。左栏始终是当前登录的机器，右栏是选中的对端。在哪一栏选文件或目录，就从哪一栏按 `F5` 复制到另一栏的当前目录。

## 命令速查

新安装只需记住一个命令 `pto-yazi-lan`。不加参数会在终端选择对端并打开双栏面板。

| 日常操作 | 命令 |
| --- | --- |
| 查看可连接的服务器 | `pto-yazi-lan --list` |
| 立即同步并查看新列表 | `pto-yazi-lan --sync --list` |
| 打开指定对端 | `pto-yazi-lan --peer b` |
| 检查 SSH、线路和目录权限 | `pto-yazi-lan --check --peer b` |
| 为当前账户启用 Yazi 插件 | `pto-yazi-lan --enable-plugin` |
| 升级程序 / 查看版本 / 查看完整帮助 | `--update` / `--version` / `--help` |

| 节点和拓扑操作 | 在 `pto-yazi-lan` 后填写 |
| --- | --- |
| 中心新增节点或更新其地址、端口 | `--node NAME --file FILE`，按需加 `--lan`、`--netbird`、`--port` 等 |
| 建立或更新双向连接 | `--link A B --route lan-first --file FILE` |
| 移除双向连接 | `--unlink A B --file FILE` |
| 下线节点并移除其连接 | `--remove-node NAME --file FILE` |
| 校验中心清单 | `--check-topology --file FILE` |
| 在传输服务器上登记本机身份和清单来源 | `--bind NAME --source URL或绝对路径` |
| 重新设置本机的定时同步并立即拉取 | `--setup-sync` |

整机安装默认每分钟同步一次。当前账户需要立即刷新时可运行 `pto-yazi-lan --sync`。0.5.0 起可直接运行 `pto-yazi-lan --update` 升级程序，整机更新需要额外权限时会调用 `sudo`。0.4.0 及更早版本首次升级需再运行一次原安装命令。一键安装器会为已有安装保留其原先创建的旧命令链接，现有脚本可以继续运行。新脚本统一使用上表的参数。

## 管理员部署与更新拓扑

可以用现有的 NetBird 中心服务器发布拓扑。A、B 各自通过 HTTPS 拉取，在本机保存一份供所有账户读取的副本。用户从任一端发起复制时，文件经 SSH 在 A 和 B 之间传输，优先使用拓扑允许的机房内网。

| 在哪台机器 | 文件 | 用途 |
| --- | --- | --- |
| NetBird 中心 | `/srv/yazi/topology.json`（示例） | 管理员维护的原件，记录各节点的地址、SSH 端口、主机公钥和允许的连接 |
| 每台传输服务器 | `/etc/yazi-lan/deployment.json` | 本机节点名、中心清单的 HTTPS 地址和可选的 CA 证书路径 |
| 每台传输服务器 | `/var/lib/yazi-lan/topology.json` | 最近一次成功拉取的清单，供整机用户共用，由同步程序原子替换 |

**在中心发布。** 在中心安装管理命令，将下面的地址和端口换成实际值。在现有 HTTPS 站点上把 `/yazi/topology.json` 映射到 `/srv/yazi/topology.json`，允许各节点通过 NetBird 只读访问。目录可由管理员指定，HTTPS 证书需要匹配访问时使用的名称。NetBird 管理服务本身无需改动。

```bash
curl -fsSL https://github.com/pypto-tools/pto-yazi-lan/releases/latest/download/install.py | sudo python3 - --system
sudo pto-yazi-lan --node a --file /srv/yazi/topology.json --lan 10.20.0.11 --netbird 100.80.0.11 --port 22
sudo pto-yazi-lan --node b --file /srv/yazi/topology.json --lan 10.20.0.22 --netbird 100.80.0.22 --port 2222
sudo pto-yazi-lan --link a b --file /srv/yazi/topology.json --route lan-first
```

登记节点时，可加 `--host-key-file /path/to/node-host-key.pub` 指定已核对的该节点 SSH 主机公钥，也可用 `--interface eno1` 指定机房出口网卡。省略网卡时程序按本机登记的机房 IP 查找出口。

**在 A 和 B 上接入。** 在 A 执行以下命令。在 B 将 `--node a` 改成 `--node b`，中心 URL 相同。示例域名需换成各节点能通过 NetBird 访问的实际地址。使用私有 CA 时追加 `--ca-file /etc/ops/ca.pem`。

```bash
curl -fsSL https://github.com/pypto-tools/pto-yazi-lan/releases/latest/download/install.py | sudo python3 - \
  --system --node a --topology https://netbird-center.example.org/yazi/topology.json
pto-yazi-lan --list
```

程序已安装时，可以直接登记或更改本机绑定。把 `a` 和示例 URL 换成本机的节点名和实际清单地址。

```bash
sudo pto-yazi-lan --bind a --source https://netbird-center.example.org/yazi/topology.json
```

整机安装会立即拉取一次，并启用 `pto-yazi-lan-sync.timer`，默认每分钟检查一次。没有运行 systemd 的机器可使用安装器生成的 cron 任务，由已启动的 cron 服务执行。使用其他作业管理器时，可安排每分钟运行 `/usr/local/bin/pto-yazi-lan --sync`。已用 0.3.0 登记中心 URL 的机器，重新执行整机安装命令并加 `--system` 即可沿用原登记并启用同步。

**变更和断网。** 例如在中心修改 B 的端口，各节点下次同步后，用户用 `pto-yazi-lan --list` 可看到新端口和修订号，面板按 `r` 按新配置重连。已建立的 SSH 会话继续当前传输。中心不可达、证书校验失败或新文件内容无效时保留旧副本，下一轮会重试。首次需要成功拉取一次。节点离线期间，新增或移除的连接会在恢复同步后生效。

```bash
# 在中心更新
sudo pto-yazi-lan --node b --file /srv/yazi/topology.json --port 2223
sudo pto-yazi-lan --check-topology --file /srv/yazi/topology.json

# 在任意已部署的节点立即拉取或查看定时任务
pto-yazi-lan --sync
systemctl list-timers pto-yazi-lan-sync.timer
journalctl -u pto-yazi-lan-sync.service -n 10
```

| 配置 | 效果 |
| --- | --- |
| `lan-first` | 优先验证机房路由，线路不可达时使用双方已登记的 NetBird 地址 |
| `lan-only` | 限定机房内网 |
| `netbird-only` | 限定 NetBird |
| `--unlink a b` | 从双方的面板移除这条连接，下次重连生效 |
| `--remove-node b` | 下线 B，并在同一次更新中删除它的所有连接 |

**已有共享清单的部署。** `--topology /mnt/ops/yazi-topology.json` 会直接读取指定文件，适用于共享磁盘或已有的配置分发工具。部署账户对原件有写权限时，可在 `--system --node a` 安装命令里附加 `--lan`、`--netbird`、`--port` 等参数一并登记本机。HTTPS 接入时，管理员在中心原件上登记和更改节点。单独指定 `--deployment /path/to/site.json` 的独立客户端会在打开或重连时尝试更新，并将副本存入同目录的 `site.json.d/topology.json`。

管理命令会校验整份清单、原子更新文件，并在实际修改时增加 `revision`。也可以在管理端编辑以下格式的 JSON，发布前用 `pto-yazi-lan --check-topology --file 文件` 验证。

```json
{
  "schema": 1,
  "revision": 1,
  "nodes": {
    "a": {"lan": "10.20.0.11", "netbird": "100.80.0.11", "port": 22},
    "b": {"lan": "10.20.0.22", "netbird": "100.80.0.22", "port": 2222}
  },
  "links": [{"nodes": ["a", "b"], "route": "lan-first"}]
}
```

**用户认证和目录权限。** `pto-yazi-lan --peer b` 使用当前用户的 OpenSSH 配置和密钥，通常登录对端的同名账户。`pto-yazi-lan --peer b --user backup` 可以使用另一个已授权账户。管理员登记了 `host_key` 时，内网与 NetBird 均严格核对该公钥。省略时使用 OpenSSH 已信任的主机密钥或 SSH CA。若目标 SSH 服务拒绝当前密钥，面板会报告认证失败。拓扑连接控制应用的对端选择，实际网络访问和文件权限由防火墙、SSH 服务和文件系统管理。

面板打开时会列举两栏目录，并在各自目录里独占新建、随即删除一个空的 `.yazi-lan-check-*` 文件，分别显示能否接收文件。进入其他目录后，在确认复制时检查所选目标。也可以运行 `pto-yazi-lan --peer b --check --local-dir /data/in --remote-dir /data/out`。两端检查都通过时退出码为 0，只读目录会单独报告。具体源文件可读性和同名文件覆盖权限以实际操作为准。B 上的用户发起传输时，使用 B 上该用户自己的凭据独立验证。

整机部署后用户可直接运行 `pto-yazi-lan`。要接入自己的 Yazi 快捷键，执行一次 `pto-yazi-lan --enable-plugin`。

## 个人一键安装

在 A 和 B 上各用自己的登录账户执行一次。安装需要 Linux、`curl`、Python 3.9 或更高版本（含 `venv` 和 `ensurepip`），并能访问 GitHub Releases 和所配置的 Python 软件包索引。

```bash
curl -fsSL https://github.com/pypto-tools/pto-yazi-lan/releases/latest/download/install.py | python3 -
```

安装器将命令放在 `~/.local/bin/pto-yazi-lan`，将专用 Python 环境放在 `~/.local/share/yazi-lan/versions/`。如果终端提示找不到命令，可执行 `export PATH="$HOME/.local/bin:$PATH"`。给 `PYTHONUSERBASE` 或 `XDG_DATA_HOME` 设置了自定义路径时，安装器会使用对应目录。安装完成后，运行 `pto-yazi-lan --help` 可检查命令是否可用。

Yazi 插件默认装到 `~/.config/yazi/plugins/lan-copy.yazi`，也支持 `YAZI_CONFIG_HOME` 和 `XDG_CONFIG_HOME`。首次安装会为新配置绑定 `F5`。已有的插件或快捷键文件会保留，下面的快捷键示例可用于合并自定义配置。

也可以从同一个 Release 下载安装器和 wheel 后运行 `python3 install.py --wheel ./pto_yazi_lan-0.7.0-py3-none-any.whl`。pip 会安装依赖，下载文件可用同一 Release 中的 `SHA256SUMS` 校验。

## 从源码安装

在源码目录中执行以下命令，安装 `pto-yazi-lan`、Yazi 插件和快捷键配置。

```bash
python3 -m venv ~/.local/share/yazi-lan/venv
source ~/.local/share/yazi-lan/venv/bin/activate
python -m pip install .
```

也可以只在一台机器上运行 `python -m pip wheel --no-deps --wheel-dir dist .`，将生成的 `dist/pto_yazi_lan-0.7.0-py3-none-any.whl` 复制到对端用户的主目录。在对端创建并激活虚拟环境后，运行 `python -m pip install ~/pto_yazi_lan-0.7.0-py3-none-any.whl`。

两台机器都需要 Linux、Python 3.9 及以上、OpenSSH 客户端、iproute2，以及对方可访问的 SSH/SFTP 服务。两端装有 rsync 3.1 及以上时会使用 rsync。独立双栏面板可直接运行，从 Yazi 按 `F5` 打开需要本机装有兼容的 Yazi（已验证 26.9.1）。

## 两边分别配置对方

本节适用于未接入管理员拓扑的个人安装，0.2.0 的个人配置可继续使用。

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

在 A 或 B 上都可以运行相同的命令。只配置一个对端时自动选中，配置多个时可在终端按编号选择，也可以直接运行 `pto-yazi-lan --peer server-b` 或 `pto-yazi-lan --peer server-a`。

```bash
pto-yazi-lan --check
pto-yazi-lan
```

也可以直接用命令行指定线路，例如在 B 上运行 `pto-yazi-lan --peer server-a --lan 10.20.0.11 --interface ens192`。

例如在 B 上启动，左栏勾选 `checkpoints/`，右栏进入 A 的 `/data/incoming`。从左栏按 `F5` 和回车，目标是 A 上的 `/data/incoming/checkpoints/`。从右栏发起则会拉取到 B。另一台只需保持 SSH/SFTP 可达，传输面板可以关闭。

一键安装后，启动 `yazi`，选好本机文件并按 `F5` 即可打开传输面板。使用自定义快捷键时，先合并下方的按键配置。从源码安装时，也可以激活上述虚拟环境并用包内的配置启动。

```bash
YAZI_CONFIG_HOME="$VIRTUAL_ENV/share/yazi-lan/yazi" yazi .
```

要接入已有的 Yazi 配置，将包内 `share/yazi-lan/yazi/plugins/lan-copy.yazi` 放入自己的 Yazi 插件目录，并将以下条目加入 `keymap.toml`。两边可用同一条快捷键。原有的 `plugin lan-copy <对端>` 和 `YAZI_LAN_PEER` 也能继续使用，显式参数优先。插件通过 `PATH` 查找 `pto-yazi-lan`，也可以把 `YAZI_LAN_BIN` 设为其绝对路径。

```toml
[[mgr.prepend_keymap]]
on = "<F5>"
run = "plugin lan-copy"
desc = "在本机和对端之间复制"
```

## 传输面板

两栏标题显示节点名，亮色标题和整行光标标出当前操作的一侧。目录、链接及常见文件类型有不同图标，宽窗口还会显示文件大小。底部提示本次上传或下载的目标。要显示完整图标，在发起 SSH 的终端中选用 Nerd Font。按 `i` 可切换为基础符号。

按 `F5` 确认复制后，任务按顺序在后台执行。双栏底部显示方向、文件名、进度、速率和已传大小。rsync 显示本批次的进度，SFTP 显示当前文件的进度。复制期间可以继续浏览并追加任务，按 `t` 展开完整列表。退出或重连前可等待队列完成，或按 `x` 取消队列。

按 `p` 可在另一栏预览光标下的本地或远端项目。UTF-8 文本显示开头最多 8 KiB，二进制文件显示大小和十六进制摘要，符号链接显示目标。远端文件通过当前 SSH 连接读取。

| 按键 | 操作 |
| --- | --- |
| `Tab` | 切换本机和对端两栏 |
| `i` | 切换 Nerd Font 图标与基础符号 |
| `p`、`PgUp` / `PgDn` | 开关文件预览、滚动预览 |
| `t` | 展开或收起传输列表，展开后用 ↑↓ 滚动 |
| `↑`、`↓` 或 `k`、`j` | 移动光标 |
| `Enter`、`l` / `Backspace`、`h` | 进入目录 / 返回上级 |
| 空格 | 勾选或取消当前项，可跨目录选择 |
| `F5` 或 `c` | 把选中项复制到另一栏当前目录，未勾选时使用光标所在项 |
| 在复制提示中按 `Enter` 或 `s` | 合并目录，跳过目标已有的同名文件 |
| 在复制提示中按 `u` | 合并目录，允许更新同名文件，目标独有的文件会保留 |
| `x` | 取消当前及排队中的任务，将未完成任务的来源重新勾选 |
| `r` / `q` | 重新选路并连接 / 退出传输面板 |

## 内网和 NetBird

程序检查发起端的路由出口，将 SSH 绑定到该网卡的源地址，再验证对端的主机密钥。内网和 NetBird 入口共用同一份 SSH 主机身份。SFTP 浏览与 rsync 传输复用这次连接，对端地址来自管理端清单或个人配置。

要允许回退，在对端配置中增加 `"netbird": "对端的 NetBird IP"` 或在命令行添加 `--netbird IP`。仅当机房路由不符或 SSH 端口网络不可达时才回退。主机密钥不匹配或认证失败会显示错误。通过 NetBird 连接后，面板读取本机 `netbird status --json`，显示直连、中转或类型未知。已配回退时也可以用 `--only-lan` 为本次任务限定线路。

## 目录和中断

默认用 rsync 递归传输，保留文件的权限、修改时间及符号链接。重试时 rsync 可以利用目标端 `.rsync-partial` 内的数据。首版在高速内网上直接传目录，省去生成临时归档的读写开销。

当对端账户只允许 SFTP，或任一端缺少 rsync 时，面板会显示 `SFTP`。此模式逐文件复制，用同目录临时文件在完整接收后发布。中断后可用跳过同名文件的方式重试，当前未完成的文件从头传输。远端覆盖已有文件需要 SFTP 服务支持 OpenSSH 的 POSIX rename 扩展。
