# 小狗项目准备工作 —— Windows WSL 版

> 大头狗（VbotBaboEDU）在 Windows + WSL2 下的环境配置与启动全流程
> 适用：Windows 11（WSLg 图形自带）；Windows 10 见坑位表
> 姊妹篇：《小狗项目准备工作.md》（Mac + UTM 路线，含仿真原理与官方接口红线）
> 整理日期：2026-10-18

---

## 一、为什么是 WSL（以及和 UTM 路线的分工）

- 课程主推：`wsl --install` 两条命令开环境，ROS 2 Humble 官方支持 Ubuntu 22.04
- WSL2 = 真 Linux 内核轻量虚拟机；**WSLg 让 Linux GUI 窗口直接弹在 Windows 桌面**（海龟、MuJoCo viewer 都有窗口，不需要 VNC/转发）
- 与 Mac/UTM 路线共用同一套脚本与 `~/dog_sim` 仿真资产，命令完全一致

| | Windows + WSL2 | Mac + UTM |
|---|---|---|
| GUI 窗口 | WSLg 原生弹窗 | 虚拟机控制台窗口 |
| USB 设备进 Linux | usbipd-win 挂载 | UTM 顶栏 USB 图标 |
| 连局域网真狗 | mirrored 网络模式 | UTM 桥接模式 |
| 磁盘性能 | ext4.vhdx（NTFS 上），中等 | 注意 ExFAT 移动硬盘放大坑 |

---

## 二、安装 WSL 与 Ubuntu 22.04（PowerShell，管理员）

```powershell
wsl --install -d Ubuntu-22.04     # 22.04 配 Humble 是固定搭档，别装错版本
# 装完按提示重启一次；首次进入设 Linux 用户名/密码（记牢）
wsl --set-default Ubuntu-22.04
wsl -e bash                      # 以后进环境
```

进 WSL 后先开 systemd（ros daemon、sshd 等更稳）：

```bash
sudo tee /etc/wsl.conf <<'EOF'
[boot]
systemd=true
EOF
# 然后回 PowerShell 执行：wsl --shutdown，再重新进入生效
```

换清华 apt 源（**x86 电脑是 amd64，走 archive 路径**；ARM 版 Windows 才走 ports）：

```bash
sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak
sudo sed -i 's@archive.ubuntu.com@mirrors.tuna.tsinghua.edu.cn@g; s@security.ubuntu.com@mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list
sudo apt-get update
```

---

## 三、ROS 2 Humble + 验收

直接复用项目脚本（脚本内已兼容 amd64/arm64 与清华 ROS 源）：

```bash
sudo apt-get install -y software-properties-common curl
sudo curl -sSL --retry 3 https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu jammy main" | sudo tee /etc/apt/sources.list.d/ros2.list
sudo apt-get update
sudo apt-get install -y ros-humble-desktop ros-dev-tools python3-colcon-common-extensions
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
```

验收（WSLg 窗口直接弹在 Windows 桌面）：

```bash
ros2 run turtlesim turtlesim_node      # 终端1：海龟窗口
ros2 run turtlesim turtle_teleop_key   # 终端2：方向键开龟（焦点在此终端）
```

---

## 四、MuJoCo + 大头狗仿真

```bash
pip3 install -i https://pypi.tuna.tsinghua.edu.cn/simple mujoco
git clone --depth 1 https://ghfast.top/https://github.com/VitaDynamics/vbot-lab.git   # 国内加速；直连能通就去掉前缀
R=~/vbot-lab/assets/robots/foot_quadruped/
mkdir -p ~/dog_sim
sed "s|package:/VbotBaboEDU/|$R|g; s|package://VbotBaboEDU/|$R|g" $R/urdf/VbotBaboEDU.urdf > ~/dog_sim/dog_abs.urdf
```

`~/dog_sim/mjdog2.py`（与 Mac 版同一份：URDF → `mj_saveLastXML` 导 MJCF → 正则注入 14 个 position 执行器 → viewer）：

```bash
cat > ~/dog_sim/run_viewer.sh <<'EOF'
#!/bin/bash
cd ~/dog_sim
python3 mjdog2.py        # WSLg 下 DISPLAY 已自动设置，前台跑即可，关掉终端=关窗口
EOF
chmod +x ~/dog_sim/run_viewer.sh
bash ~/dog_sim/run_viewer.sh
```

- Windows 桌面弹出 **MuJoCo : VbotBaboEDU** 窗口 → 右栏 Control = 14 根电机滑杆，拖=狗动；左栏 Reset 复原
- 独显/核显装好 Windows 驱动时，WSLg 走 GPU 半虚拟化（/dev/dxg），viewer 比 Mac 虚拟机里的软件渲染更顺
- 编译报网格找不到 = `package://` 没改干净，重跑 sed

---

## 五、现场直连真狗（WSL 网络特调）

WSL2 默认 NAT，**够不到直连网线的狗**（192.168.126.0/24）。开 mirrored 镜像网络：

```bash
sudo tee -a /etc/wsl.conf <<'EOF'
[network]
mirrored=true
EOF
# PowerShell：wsl --shutdown 后重进
```

mirrored 模式下 WSL 与 Windows 共用网卡视图，之后：

1. **Windows 侧**：USB-C 转网口接狗；该适配器网卡静态 IP `192.168.126.100/24`（狗固定 `.2`，电脑禁占 .2；只改这块网卡）
2. **WSL 内验证**：`ping 192.168.126.2` → `ssh vbot@192.168.126.2`（初始密码 vbot，登入即换密钥）
3. **只读发现（官方红线：禁止发控制 topic 试探）**：
   ```bash
   ros2 topic list | head -30
   timeout 15s ros2 topic echo /bms_state          # 电池
   timeout 15s ros2 topic echo /locomotion/status  # 姿态/心跳
   ```
4. **首动纪律**：狗离地/上支架；小值 `/locomotion/velocity_command` 起步；请求设超时，`/locomotion/action_report` 无终态不盲重发
5. Windows 防火墙：直连网卡上放行 ROS/DDS（或现场临时关该网卡防火墙），否则 topic 单通/不通
6. 非网口 USB 设备（手柄/雷达直连等）：Windows 装 **usbipd-win**，`usbipd attach --wsl --busid <id>` 挂进 WSL

---

## 六、坑位表

| 坑 | 解 |
|---|---|
| Windows 10 无 WSLg | 装 VcXsrv/X410 做 X server，`export DISPLAY=$(ip route list default | awk '{print $3}'):0` |
| 改了 wsl.conf 不生效 | 必须 PowerShell `wsl --shutdown` 再进 |
| 项目文件放 /mnt/c | 慢 5–10 倍；放 WSL 家目录（`~/`），Windows 侧用 `\\wsl$\` 访问 |
| 版本装成 24.04/20.04 | Humble 只认 22.04；错了 `wsl --install -d Ubuntu-22.04` 并列装 |
| `ros2` 命令找不到 | 新终端自动 source；当前终端手动 `source /opt/ros/humble/setup.bash` |
| mirrored 模式个别 VPN/代理冲突 | 现场关 VPN；代理软件排除 192.168.126.0/24 |
| GUI 窗口不弹 | 确认 Win11 且 `wsl --version` 有 WSLg；`wsl --update` 后 `wsl --shutdown` |

---

## 七、速查卡（WSL 版）

| 目的 | 命令 |
|---|---|
| 进环境 | PowerShell `wsl -e bash` |
| 海龟验收 | `ros2 run turtlesim turtlesim_node` |
| 起狗仿真 | `bash ~/dog_sim/run_viewer.sh` |
| 连真狗 | `ssh vbot@192.168.126.2`（初始密码 vbot） |
| 看电池 | `timeout 15s ros2 topic echo /bms_state` |
| 控制输入（慎） | `/locomotion/velocity_command`、`/locomotion/joy` |
| 重启 WSL 配置 | PowerShell `wsl --shutdown` |

> 安全备注：真狗初始密码 vbot/vbot 为官方公开文档内容，接入后应即刻修改；个人机器密码一律不入仓库。
