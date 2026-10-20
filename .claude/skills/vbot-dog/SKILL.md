---
name: vbot-dog
description: 维他动力大头狗 EDU（VbotBaboEDU / foot_quadruped）快速部署与开发指南。当用户要连接机器狗、让狗动起来、查询狗的状态、或跑导盲案例（如"找空位并语音播报"）时使用。覆盖：连接自检、现成能力层速查（不需要自己开发的部分）、应用层最小代码模板、空位播报案例端到端、 debug 坑表。对机器人零基础的开发者友好。
---

# vbot-dog：大头狗 EDU 快速部署 skill

适用机型：维他动力 Vbot EDU（`foot_quadruped`，应用 V1.6.0，载板地瓜 S100P 128TOPS）。
**只有 EDU 版开放 SSH/SDK**；普通消费版连不上，先确认。

## 三条铁律（先读，保命）

1. **只读发现红线**：检查环境只用订阅/`aorta topic list`/限时 echo；**禁止靠发布控制 topic 来试探**。
2. **重计算不上狗**：开发者配额 1 核 + 4GB。本 skill 的案例脚本只是"调服务+说话"，够轻，可在狗上跑；任何感知/推理节点放伴随电脑。
3. **动狗之前**：狗离地或上支架、急停手段在手边（标签键/语音词"停下"）、首次速度最小。扶鞍状态下谨防狗进 Passive 卸力。

## 1. 连接与自检（5 步）

```bash
# ① 电脑网卡静态 IP 192.168.126.100/24（狗固定 .2，禁占 .2），USB-C 转网口直连
# ② 登录（初始密码 vbot，登入即改 + 配密钥）
ssh vbot@192.168.126.2
# ③ 设备 shell 环境（写进 ~/.bashrc，顺序不能乱）
export PATH="/opt/vita/aorta/bin:$PATH"
export ZENOH_SESSION_CONFIG_URI=/opt/vita/aorta/edu/edu_session.json5
source /opt/vita/ros/humble/setup.bash
source /opt/vita/aorta/ros/local_setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_DOMAIN_ID=178 ROS_LOCALHOST_ONLY=1
# ④ 只读自检
command -v aorta && command -v ros2
timeout 15s aorta topic list
# ⑤ 限时采样验证数据在流
timeout 15s ros2 topic echo /bms_state --no-daemon --once
```

跨机（伴随电脑）访问：Zenoh client 连 `tcp/192.168.1.2:7447`（凭据在板端 `/app_param/zenoh/s100_session.json5`），或 SSH 隧道。
⚠️ `test -r /opt/vita/aorta/edu/edu_session_peer.json5` 为假时会话仅 client 模式，**允许行以外的名称被静默丢弃（表现为超时不是报错）**——先查这个文件。

## 2. 现成能力层（不用你开发，直接调）

| 想干什么 | 现成接口 | 备注 |
|---|---|---|
| 看电量/IMU/姿态 | 订阅 `/bms_state` `/imu_raw` `/locomotion/status` | 只读 |
| 看人/看物体 | 订阅 `/perception/poses`(17 关键点) `/perception/detections2d` | 原厂预部署 |
| **问"空椅子/门口在哪"** | service `SemanticNavQuery`（function_msgs）：prompt + ROI 或 base64 图 → 方向+距离+点云 | **空位案例的核心** |
| 通用图文问答 | service `OmniPrompt`：system+user prompt + 图 → 文本 | 需传图 |
| 听人说话 | 订阅 `/speech/asr_result`（处理 reject/中间文本）；唤醒词+DOA | |
| **让狗说话** | service `/set_speak`：mode=1 + human_language_text | 盲人就靠这条 |
| 走：伴随/跟随/召唤/到点/语义导航 | service `ControlFollowing` mode 8/1/4/3/6 + `SetFollowSpeed` 限速 | 导盲主用 8 和 3 |
| 速度直控 | topic `/vel_cmd`（死区 0.3！区间 [0.3,1.2]/[-0.3,-1.2]） | 新手别用，用 ControlFollowing |
| 站起/趴下/牵引模式 | `/sm/action/lowlevel`、`/locomotion/set_run_mode` | 见 references/interfaces.md |
| 建图/定位 | `/slam/set_slam_mode` mode 2 建/存、3 加载定位；状态看 `/slam/status` | LOST 必须停车+语音 |
| 多通道编排（语音+灯+头朝向） | action `/rcp/execute_task`（DAG） | 一个行为一条控制路径，别混用 |

完整字段表与命令原文：[references/interfaces.md](references/interfaces.md)。

## 3. 应用层最小模板（代码层面让狗动）

不会写 ROS 也能动狗（CLI 一把梭，**在狗上执行**）：

```bash
# 说话（SetSpeak 在 function_msgs，已核对 vbot_ros2_msgs 源码）
ros2 service call /set_speak function_msgs/srv/SetSpeak "{mode: 1, human_language_text: '你好，我是导盲犬', target_state: 1}"
# 伴随模式 + 限速（先离地试！）
ros2 service call /locomotion/control_following function_msgs/srv/ControlFollowing "{target_state: 1, mode: 8, req_id: 'acc1', pre_check: true}"
ros2 service call /locomotion/set_follow_speed function_msgs/srv/SetFollowSpeed "{following_speed: 0.5, source: 'guide'}"
# 停
ros2 service call /locomotion/control_following function_msgs/srv/ControlFollowing "{target_state: 0, mode: 0, req_id: 'stop1'}"
```

（服务名/消息包以 `ros2 service list -t` 现场发现为准；上表为 V1.6.0 常见命名。）

写节点：rclpy 标准写法，两个要点——① 服务名用 `get_service_names_and_types()` 按**类型后缀**发现（防改名）；② 所有调用加超时与终态检查（`/locomotion/action_report` 无终态 = 结果未知，不盲重发）。模板见 [scripts/empty_seat_finder.py](scripts/empty_seat_finder.py)。

## 4. 案例：找空位 → 语音播报"这里有空位"

一键部署运行（Mac/伴随电脑执行）：

```bash
cd <本 skill 目录>/scripts && bash run_case.sh
```

它做的事：scp 脚本上狗 → ssh 源环境 → 运行。脚本逻辑（零训练、全现成接口，v2 最后十米版）：

1. 按类型后缀发现 `SemanticNavQuery` 与 `SetSpeak` 服务
2. **提前找空位**：query → 最近空座位（仿真 message=座位名；真机=VLM 答复）
3. 播报"找到空位，X 座，方位约 N 米，我带您过去" → 发 `/sim/nav_goal`（真机由 VbotBackend 翻译成 NAV_TO_POINT）
4. **最后十米确认**：到位旁再 query 一次；座位被抢 → 播报"刚被占用，换一个"并重试一次
5. 确认空 → 播报"这里有空位" + 发 `/sim/pose crouch` → 狗蹲守位旁（真机≈lie_down 预置动作）
6. 全程失败 → 诚实播报"没找到"，不瞎报

验收：狗出声且方位与实际大致相符；换 3 个不同座位摆放各试 5 次记录成功率。

## 5. Debug 坑表（先查这里再怀疑人生）

| 症状 | 真相 |
|---|---|
| 调用超时不报错 | ACL 静默丢弃：查 `edu_session_peer.json5` 是否存在 |
| 发 0.1 m/s 狗不动 | `/vel_cmd` 死区 0.3 |
| 工作站 echo 不到 topic | ROS 只在狗上（LOCALHOST_ONLY=1）；跨机走 Zenoh |
| 表情不播 | mode 是资源 ID 不是枚举（SAY_HI 枚举 32 ≠ 资源 48） |
| 导航中途狗停 | loc_status LOST：语音告知+停车+降级跟随，别硬重试 |
| RCP 任务里直接调 service 被覆盖 | 一个行为一条控制路径 |
| 服务名/包名对不上 | 用类型后缀现场发现，别硬背 |

## 6. 现场核对清单（到时逐项查，查完勾掉）

- [ ] **官方设备 Agent/MCP 是否已把 SemanticNavQuery 包成现成 tool**：若是，"对狗说帮我找空椅子"可能开箱能答；但主动触发策略、播报措辞、降级兜底仍是我们的活（案例价值不减）
- [ ] SetSpeak 服务名/消息包现场确认（`ros2 service list -t` 按类型后缀找）
- [ ] 板载 TTS 延迟与音色实测记录（M2 语音环验收数据）

## 6.5 彩蛋：海龟页指挥真狗（教学/空旷场地调试用）

turtlesim 的 teleop 和狗都说 `geometry_msgs/Twist`，所以能直接 remap：

```bash
# 方向键开真狗（在狗上或 Zenoh 跨机执行）；⚠️ /vel_cmd 死区 0.3、无避障保证
ros2 run turtlesim turtle_teleop_key --ros-args -r /turtle1/cmd_vel:=/vel_cmd
```

反向玩法——**海龟当狗的替身.avatar**：订阅 `/odometry` 转发 `/turtle1/pose`(x/y/theta)，
海龟窗口实时镜像狗的位置朝向，投影讲解/远程观察好用：

```python
# turtle_avatar.py（rclpy 十行桥）：/odometry -> /turtle1/pose
# pose.x=odom.x, pose.y=odom.y, pose.theta=yaw(odom.quaternion)
```

纪律：remap 控狗只许**空旷场地+离地/有人扶+急停在手**；正式导盲永远走 ControlFollowing，不走 vel_cmd。

## 7. 仿真环境（不上真机也能练案例）

仓库 `simulation/` 目录 = 空位案例的 MuJoCo 仿真 + 假感知服务 + 网页编排：

```bash
cd simulation && bash fetch_assets.sh        # 拉狗网格+URDF，生成带执行器 MJCF（56MB，不入 git）
source <ROS 环境> && source <vbot_ros2_msgs 的 install/setup.bash>
python3 run_sim.py [--viewer]                # 物理+假 SemanticNavQuery/SetSpeak+编排HTTP :8001
```

- **同构关键**：假服务与真狗**同类型同字段**（function_msgs），`empty_seat_finder.py` 零改动通吃仿真/真机
- 网页编排：guide_bot 看板新增「空位仿真编排」面板（人坐下/起身/走开/回来、跑案例、实时播报流），地址 `http://<主机>:8001` 由面板自动探测
- 触发策略/降级/话术单测都在仿真里做（S3 失败注入：改 sim_nodes 的 success 概率）；**步态不仿**（狗站立观察，移动用运动学，规矩见 docs/controllers.md）

## 8. 延伸

- 导盲整体方案（架构/里程碑/风险）：仓库根 `导盲落地方案.md`
- 环境搭建（Mac/UTM、Windows/WSL）：仓库根两份《小狗项目准备工作》
- 接口全文与现场清单：飞书《大头机器狗导盲应用开发手册》
