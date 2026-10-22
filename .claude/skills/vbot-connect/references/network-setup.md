# VBOT EDU 网络配置与分层排错

只读取与当前操作系统、当前接入模式有关的章节。修改网络配置前先记录原连接名称和原配置，以便恢复。

## 接入选择

| 模式 | 机器狗入口 | 开发电脑 | 适合场景 |
|---|---|---|---|
| Type-C 有线 | `192.168.126.2` | `192.168.126.100/24`，网关/DNS 留空 | 高频运控、部署、现场调测 |
| 板载 WiFi（V1.6.0+） | 通常 `192.168.127.2` | DHCP 获取 `192.168.127.x` | 轻量调试、移动演示 |

`192.168.127.10` 是主控网关板，不是日常 SSH 的计算主板。

## Windows

以管理员 PowerShell 先找出实际外接网卡，不能照抄“以太网”：

```powershell
Get-NetAdapter | Sort-Object Status, Name | Format-Table Name, InterfaceDescription, Status, LinkSpeed
Get-NetIPConfiguration -InterfaceAlias "以太网"
```

确认名称后配置有线静态地址。若该接口已有其他 IPv4，先展示并让用户判断，避免删除仍需使用的地址：

```powershell
New-NetIPAddress -InterfaceAlias "以太网" -IPAddress 192.168.126.100 -PrefixLength 24
```

不要为这条直连链路填写默认网关或 DNS。若命令提示地址已存在，先用 `Get-NetIPAddress -InterfaceAlias "以太网" -AddressFamily IPv4` 核对，不要重复添加。

## Linux / WSL

WSL 通常不能直接管理 Windows 物理网卡；应在 Windows PowerShell 中配置，再从 WSL 验证路由。原生 Linux 可用 NetworkManager：

```bash
nmcli device status
nmcli connection show
sudo nmcli connection modify "<连接名>" \
  ipv4.method manual ipv4.addresses 192.168.126.100/24 \
  ipv4.gateway "" ipv4.dns "" ipv4.never-default yes
sudo nmcli connection up "<连接名>"
```

只想做临时现场测试且明确接口名时，可添加临时地址；不要清空接口上的其他地址：

```bash
sudo ip address add 192.168.126.100/24 dev <接口名>
```

恢复时删除同一条临时地址：

```bash
sudo ip address del 192.168.126.100/24 dev <接口名>
```

## macOS

先找硬件端口与设备名：

```bash
networksetup -listallhardwareports
networksetup -getinfo "USB 10/100/1000 LAN"
```

图形界面优先：系统设置 → 网络 → 对应 USB 网卡 → TCP/IP → 配置 IPv4“手动”；IP `192.168.126.100`，子网掩码 `255.255.255.0`，路由器留空。不要改正在提供外网的 WiFi 服务。

## WiFi 直连

1. 确认固件为 V1.6.0 或更高版本，并通过设备支持的方式开启开发者 WiFi 调试。
2. 连接设备铭牌或出厂卡注明的 `VBOT-EDU-XXXX` 热点；不要猜测或记录密码。
3. 确认电脑通过 DHCP 获得 `192.168.127.x`，再检查 `192.168.127.2`。
4. 如果没有获得地址，先忘记并重新连接热点，再核对固件与开发者模式；不要手动占用 `.2` 或 `.10`。

## Host Key 冲突

警告是 SSH 的中间人攻击防护，不是可忽略的噪声。先展示旧记录与设备当前提供的指纹：

```bash
ssh-keygen -F 192.168.126.2
ssh-keyscan -T 5 -t ed25519 192.168.126.2 2>/dev/null | ssh-keygen -lf -
```

只有用户通过可信渠道确认设备已更换或重装后才备份并移除旧记录：

```bash
cp ~/.ssh/known_hosts ~/.ssh/known_hosts.vbot-backup
ssh-keygen -R 192.168.126.2
ssh vbot@192.168.126.2
```

WiFi 模式替换为 `192.168.127.2`。如果无法确认设备身份，停止，不要接受新指纹。

## 症状定位

| 症状 | 优先检查 |
|---|---|
| 没有匹配网段的本机 IPv4 | 网卡选择、静态 IP 或 WiFi DHCP |
| `Network is unreachable` | 本机路由/接口未启用 |
| Ping 超时且 TCP/22 超时 | 上电、线缆/AP、目标 IP、静态地址 |
| Ping 超时但 TCP/22 开放 | ICMP 被过滤；继续 SSH |
| TCP/22 `refused` | 目标在线但 sshd 未监听/尚未启动 |
| Host Key changed | 先核实设备身份，再清旧记录 |
| `Permission denied` | 用户名、密钥、密码或服务端授权；网络已通 |
| SSH 成功但别名失败 | 用 `ssh -G <别名>` 检查最终 HostName/User/IdentityFile |
