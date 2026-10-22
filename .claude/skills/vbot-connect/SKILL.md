---
name: vbot-connect
description: 连接或诊断 VBOT 大头 EDU 的开发网络与 SSH。用户提到首次开箱、Type-C 有线、板载 WiFi、192.168.126.2、192.168.127.2、ping 不通、SSH 登录失败、免密、别名或 Host Key 冲突时使用。只处理网络与登录，不执行机器狗运动控制。
---

# VBOT EDU 网络接入与连通性检查

目标：从开发电脑安全确认到计算主板的链路、IP、TCP/22、SSH 身份与远端系统。默认只读；不要通过发送 ROS topic 或运动命令测试连接。

## 已知拓扑

- 有线 Type-C/千兆网口：开发电脑建议 `192.168.126.100/24`，计算主板 `192.168.126.2`。
- WiFi 直连（固件 V1.6.0+）：电脑通常由 DHCP 获取 `192.168.127.x`，计算主板映射入口通常为 `192.168.127.2`。
- 主控网关板是 `192.168.127.10`；它不是日常 SSH 开发主机，不要把它与计算主板混淆。
- 默认 SSH 用户为 `vbot`。初始密码可能为 `vbot`，但不要在脚本、日志或 SSH 配置中保存密码。

固件、热点名和映射地址可能因批次变化。现场结果与本表不一致时，报告检测证据并让用户核对铭牌、APP 或官方出厂卡，不要猜测凭据。

## 诊断流程

先从本 skill 目录执行只读检查：

```bash
bash scripts/check_connectivity.sh
```

脚本会自动检查有线和 WiFi 两个候选地址。已知接入方式时缩小范围：

```bash
bash scripts/check_connectivity.sh --mode wired
bash scripts/check_connectivity.sh --mode wifi
bash scripts/check_connectivity.sh --host 192.168.126.2 --user vbot
```

按层解释结果，不要只说“网络不通”：

1. **本机地址/路由**：有线应看到本机 `192.168.126.x/24`，且绝不能占用 `.2`；该网卡不应配置默认网关或 DNS。
2. **ICMP**：Ping 成功证明三层链路；Ping 失败但 TCP/22 开放时，可能只是 ICMP 被禁，SSH 仍可用。
3. **TCP/22**：开放才进入 SSH 排错；关闭或超时优先检查上电、线缆/AP、网卡、静态 IP 和目标地址。
4. **SSH**：区分未知 Host Key、Host Key 冲突、认证失败、超时和远端命令成功，给出对应动作。
5. **远端自检**：仅在现有信任与免密认证均通过时读取 `hostname`、内核、系统版本和地址；不修改机器狗。

需要配置网卡时，读取 [references/network-setup.md](references/network-setup.md)，只采用与用户操作系统和接入模式匹配的部分。

## SSH 安全门槛

- 首次连接先向用户展示 SSH 指纹，并让用户通过机身/出厂资料或可信现场渠道确认，再接受它。
- 出现 `REMOTE HOST IDENTIFICATION HAS CHANGED` 时，不要直接删除旧记录。先确认用户确实换了机器狗、重装了系统或完成了可信固件更新。
- 用户确认设备身份后，才执行 `ssh-keygen -R <IP>`；删除前备份 `~/.ssh/known_hosts`。
- 不使用 `StrictHostKeyChecking=no` 绕过验证，不把密码写进命令行，不覆盖已有私钥。
- `ssh-keygen -t ed25519` 前检查 `~/.ssh/id_ed25519` 是否存在；存在就复用或让用户选择新文件名。

## 建立免密与别名

网络和主机指纹确认后再进行：

```bash
test -f ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519 -C "vbot-developer"
ssh-copy-id vbot@192.168.126.2
```

向 `~/.ssh/config` 增量添加，不覆盖其他 Host：

```sshconfig
Host vbot
    HostName 192.168.126.2
    User vbot
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

WiFi 模式将 `HostName` 改为 `192.168.127.2`。同时使用两台狗或两种链路时，采用明确别名，例如 `vbot-a-wired`、`vbot-a-wifi`，不要让同一别名在不同配置块中歧义匹配。

配置后验证：

```bash
ssh -G vbot | grep -E '^(hostname|user|identityfile) '
ssh vbot 'hostname; uname -a; grep PRETTY_NAME /etc/os-release'
```

## 完成标准

最终报告必须明确列出：接入模式、目标 IP、本机接口/IP、Ping、TCP/22、Host Key 状态、SSH 认证、远端系统自检。只有 TCP/22 和 SSH 登录实际成功时才说“接入完成”；仅 Ping 成功时说“网络层已通，SSH 尚未完成”。

连接成功后，如用户要检查 ROS 2/Aorta 或让狗运动，切换到 `vbot-dog` skill，并继续遵守其安全红线。
