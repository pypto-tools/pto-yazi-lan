# pto-yazi-lan

在 A 和 B 上安装同一个程序，就能从任意一台发起复制。左栏始终是当前登录的机器，右栏是选中的对端。在哪一栏选文件或目录，就从哪一栏按 `F5` 复制到另一栏的当前目录。

## 管理员部署与更新拓扑

管理员维护一份拓扑清单，登记服务器自己的地址、SSH 端口，以及哪些机器之间可以传输。普通用户登录任一台机器，运行 `yazi-lan --list` 选择对端即可。每次打开面板或按 `r` 重连都会读取当前清单。文件经 SSH 在源和目标机器之间传输。

**使用共享清单。** 假设两台机器都能读取 `/mnt/ops/yazi-topology.json`，部署账户可以写入该文件及所在目录。先在 A 上执行下列命令。主机公钥文件取自本机 SSH 服务实际使用的公钥。

```bash
curl -fsSL https://github.com/pypto-tools/pto-yazi-lan/releases/latest/download/install.py | sudo python3 - \
  --system --node a --topology /mnt/ops/yazi-topology.json \
  --lan 10.20.0.11 --netbird 100.80.0.11 --port 22 \
  --host-key-file /etc/ssh/ssh_host_ed25519_key.pub
```

在 B 上使用同一条命令，将本机登记为 `--node b`，并填入 B 自己的机房 IP、NetBird IP 和 SSH 端口，例如 `10.20.0.22`、`100.80.0.22` 和 `2222`。程序会按本机登记的机房 IP 查找出口网卡。部署时也可加 `--interface eno1` 显式约束网卡。整机安装将命令放在 `/usr/local/bin`，本机标识与清单位置存于 `/etc/yazi-lan/deployment.json`。普通用户应只持有清单的读取权限。

管理员用下面的命令建立双向连接。以后在管理端更新 B 的端口或调整线路，各机下次连接就会生效。

```bash
yazi-lan-topology --file /mnt/ops/yazi-topology.json link a b --route lan-first
yazi-lan-topology --file /mnt/ops/yazi-topology.json node b --port 2223
yazi-lan-topology --file /mnt/ops/yazi-topology.json check
```

| 配置 | 效果 |
| --- | --- |
| `lan-first` | 优先验证机房路由，线路不可达时使用双方已登记的 NetBird 地址 |
| `lan-only` | 限定机房内网 |
| `netbird-only` | 限定 NetBird |
| `unlink a b` | 从双方的面板移除这条连接，下次重连生效 |
| `remove-node b` | 下线 B，并在同一次更新中删除它的所有连接 |

**使用现有管理机。** 没有共享磁盘时，可以把同一份 JSON 放在现有管理机的 HTTPS 站点。管理员在清单所在的机器上用 `node` 和 `link` 更新原文件。普通节点用 `--system --node a --topology https://ops.example.org/yazi/topology.json` 接入，B 将节点名称换成 `b`。私有 CA 可通过 `--ca-file /etc/ops/ca.pem` 指定。使用配置管理工具分发本地副本也可以，将 `--topology` 指向分发路径。HTTPS 模式每次打开或重连都需要读到管理端，已开始的复制使用已建立的 SSH 会话。

管理命令会校验整份清单、原子更新文件，并在实际修改时增加 `revision`。也可以在管理端编辑以下格式的 JSON，发布前用 `yazi-lan-topology --file 文件 check` 验证。

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

**用户认证和目录权限。** `yazi-lan b` 使用当前用户的 OpenSSH 配置和密钥，通常登录对端的同名账户。`yazi-lan b --user backup` 可以使用另一个已授权账户。管理员登记了 `host_key` 时，内网与 NetBird 均严格核对该公钥。省略时使用 OpenSSH 已信任的主机密钥或 SSH CA。若目标 SSH 服务拒绝当前密钥，面板会报告认证失败。拓扑连接控制应用的对端选择，实际网络访问和文件权限由防火墙、SSH 服务和文件系统管理。

面板打开时会列举两栏目录，并在各自目录里独占新建、随即删除一个空的 `.yazi-lan-check-*` 文件，分别显示能否接收文件。进入其他目录后，在确认复制时检查所选目标。也可以运行 `yazi-lan b --check --local-dir /data/in --remote-dir /data/out`。两端检查都通过时退出码为 0，只读目录会单独报告。具体源文件可读性和同名文件覆盖权限以实际操作为准。B 上的用户发起传输时，使用 B 上该用户自己的凭据独立验证。

整机部署后用户可直接运行 `yazi-lan`。要接入自己的 Yazi 快捷键，执行一次 `yazi-lan --enable-plugin`。

## 个人一键安装

在 A 和 B 上各用自己的登录账户执行一次。安装需要 Linux、`curl`、Python 3.9 或更高版本（含 `venv` 和 `ensurepip`），并能访问 GitHub Releases 和所配置的 Python 软件包索引。

```bash
curl -fsSL https://github.com/pypto-tools/pto-yazi-lan/releases/latest/download/install.py | python3 -
```

安装器将命令放在 `~/.local/bin/yazi-lan`，将专用 Python 环境放在 `~/.local/share/yazi-lan/versions/`。如果终端提示找不到命令，可执行 `export PATH="$HOME/.local/bin:$PATH"`。给 `PYTHONUSERBASE` 或 `XDG_DATA_HOME` 设置了自定义路径时，安装器会使用对应目录。安装完成后，运行 `yazi-lan --help` 可检查命令是否可用。

Yazi 插件默认装到 `~/.config/yazi/plugins/lan-copy.yazi`，也支持 `YAZI_CONFIG_HOME` 和 `XDG_CONFIG_HOME`。首次安装会为新配置绑定 `F5`。已有的插件或快捷键文件会保留，下面的快捷键示例可用于合并自定义配置。

也可以从同一个 Release 下载安装器和 wheel 后运行 `python3 install.py --wheel ./yazi_lan-0.3.0-py3-none-any.whl`。pip 会安装依赖，下载文件可用同一 Release 中的 `SHA256SUMS` 校验。

## 从源码安装

在源码目录中执行以下命令，安装 `yazi-lan`、Yazi 插件和快捷键配置。

```bash
python3 -m venv ~/.local/share/yazi-lan/venv
source ~/.local/share/yazi-lan/venv/bin/activate
python -m pip install .
```

也可以只在一台机器上运行 `python -m pip wheel --no-deps --wheel-dir dist .`，将生成的 `dist/yazi_lan-0.3.0-py3-none-any.whl` 复制到对端用户的主目录。在对端创建并激活虚拟环境后，运行 `python -m pip install ~/yazi_lan-0.3.0-py3-none-any.whl`。

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

在 A 或 B 上都可以运行相同的命令。只配置一个对端时自动选中，配置多个时可在终端按编号选择，也可以直接运行 `yazi-lan server-b` 或 `yazi-lan server-a`。

```bash
yazi-lan --check
yazi-lan
```

也可以直接用命令行指定线路，例如在 B 上运行 `yazi-lan server-a --lan 10.20.0.11 --interface ens192`。

例如在 B 上启动，左栏勾选 `checkpoints/`，右栏进入 A 的 `/data/incoming`。从左栏按 `F5` 和回车，目标是 A 上的 `/data/incoming/checkpoints/`。从右栏发起则会拉取到 B。另一台只需保持 SSH/SFTP 可达，传输面板可以关闭。

一键安装后，启动 `yazi`，选好本机文件并按 `F5` 即可打开传输面板。使用自定义快捷键时，先合并下方的按键配置。从源码安装时，也可以激活上述虚拟环境并用包内的配置启动。

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

程序检查发起端的路由出口，将 SSH 绑定到该网卡的源地址，再验证对端的主机密钥。内网和 NetBird 入口共用同一份 SSH 主机身份。SFTP 浏览与 rsync 传输复用这次连接，对端地址来自管理端清单或个人配置。

要允许回退，在对端配置中增加 `"netbird": "对端的 NetBird IP"` 或在命令行添加 `--netbird IP`。仅当机房路由不符或 SSH 端口网络不可达时才回退。主机密钥不匹配或认证失败会显示错误。通过 NetBird 连接后，面板读取本机 `netbird status --json`，显示直连、中转或类型未知。已配回退时也可以用 `--only-lan` 为本次任务限定线路。

## 目录和中断

默认用 rsync 递归传输，保留文件的权限、修改时间及符号链接。重试时 rsync 可以利用目标端 `.rsync-partial` 内的数据。首版在高速内网上直接传目录，省去生成临时归档的读写开销。

当对端账户只允许 SFTP，或任一端缺少 rsync 时，面板会显示 `SFTP`。此模式逐文件复制，用同目录临时文件在完整接收后发布。中断后可用跳过同名文件的方式重试，当前未完成的文件从头传输。远端覆盖已有文件需要 SFTP 服务支持 OpenSSH 的 POSIX rename 扩展。
