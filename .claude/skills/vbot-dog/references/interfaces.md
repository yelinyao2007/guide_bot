# Vbot EDU 接口速查（V1.6.0 / foot_quadruped）

> 事实来源：飞书《大头机器狗导盲应用开发手册》+ vbot-lab 46 路由表。字段以 `ros2 interface show <type>` 现场为准。

## 1. 环境（狗上 ~/.bashrc，顺序固定）

```bash
export PATH="/opt/vita/aorta/bin:$PATH"
export ZENOH_SESSION_CONFIG_URI=/opt/vita/aorta/edu/edu_session.json5
source /opt/vita/ros/humble/setup.bash
source /opt/vita/aorta/ros/local_setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_DOMAIN_ID=178
export ROS_LOCALHOST_ONLY=1
```

跨机：Zenoh client `tcp/192.168.1.2:7447`，用户 `vita01-datou`，密码板端 `/app_param/zenoh/s100_session.json5`。

## 2. 只读 topic（订阅）

| topic | 类型 | 关键字段 |
|---|---|---|
| `/bms_state` | lowlevel_msg/msg/BmsState | soc_percent、告警、单体电压 |
| `/imu_raw`、`/lidar_imu` | sensor_msgs/msg/Imu | |
| `/locomotion/status` | — | posture/motion/current_action/heartbeat_seq |
| `/locomotion/action_report` | — | terminal_status/req_id/error_code（**无终态=未知，不盲重发**） |
| `/perception/poses` | vision_msgs/msg/PoseDetection | class_id(0=人)、keypoints[17]、score |
| `/perception/detections2d` | vision_msgs/msg/Detection2DArray | |
| `/lidar_points` | sensor_msgs/msg/PointCloud2 | 按 fields/point_stride 解码；**脚下 -0.25° 以下盲区** |
| `/lidar_diagnostic_status` | diagnostic_msgs/msg/DiagnosticArray | OK/WARN/ERROR/STALE |
| `/image_left_raw/h265_undistort` | foxglove_msgs/msg/CompressedVideo | H.265 固定 1280×720 |
| `/uwb/data` | uwb_location/msg/UWB | distance/angle/pos_confidence/**has_living_body**/has_head_touch |
| `/uwb/state` | uwb_msgs/msg/Status | battery_percentage、state IDLE/CONNECTED/RANGING |
| `/uwb/head_touch` | aorta_msgs/msg/HeadTouchEvent | 摸头计数 |
| `/slam/status` | slam_msgs/msg/SlamStatus | operation_mode/loc_status/map_status/current_map_name（LOST 要停车） |
| `/odometry` + `/tf` | nav_msgs/msg/Odometry | |
| `/speech/asr_result` | — | transcript/source/reject/confidence（**不对中间文本执行动作**） |
| 唤醒 | speech_msgs/msg/WakeupInfo | doa_angle 0-360（-1 不可用） |

## 3. 服务 / 动作（写，白名单内才用）

### SemanticNavQuery（function_msgs/srv/SemanticNavQuery）——空位案例核心
请求二选一：
- 图模式：`prompt` + `base64_image` + `timestamp`
- **ROI 模式（推荐，免解码视频流）**：`prompt` + `camera_frame_id`(stereo_left/right) + `roi_x_min/x_max/y_min/y_max`（720p 全幅 = 0,1280,0,720）

响应：`success / direction(机身系向量) / confidence / estimated_distance_m / hor_fov / ver_fov / point_count / cloud`
语义：VLM 看图 + 查 SLAM 地图 → "最近的空椅子/门口"方向距离。**依赖已建图+已定位。**

### OmniPrompt（function_msgs/srv/OmniPrompt）
`system_prompt + user_prompt + sensor_msgs/Image[] → response_text`。通用问答，需传图。

### ControlFollowing（function_msgs/srv/ControlFollowing）
`target_state`(0 停/1 启动) + `mode` + `req_id` + `pre_check`：
0 STOP · 1 FOLLOWING 跟随 · 2 ROAMING · 3 NAV_TO_POINT(x/y/yaw, stop_distance, max_xvel 默认 0.5) · 4 COME_TO_ME · 5 WALK · 6 SYNC_SEMANTIC_NAV · 7 ASYNC_SEMANTIC_NAV · 8 ACCOMPANY 伴随（导盲主形态）
配套：`SetFollowSpeed`(following_speed+source→applied_speed，**导盲限速写死 ~0.5-1.0**)、`FollowStatus`。

### 状态机/运控模式
```bash
# 急停（Passive，会卸力！扶鞍时慎用）
ros2 service call /sm/action/lowlevel software_msgs/srv/LowlevelAction "{target_state: 1, mode: 0}"
# 趴下
... "{target_state: 1, mode: 4, req_id: 'lie_down', pre_check: false}"
# 牵引模式（人手可推）
ros2 service call /locomotion/set_run_mode function_msgs/srv/SetRunMode "{target_state: 1, mode: 0, req_id: 'enter_rltrot', pre_check: false}"
# 基础运控
... "{target_state: 1, mode: 2, req_id: 'enter_rl', pre_check: false}"
```

### 速度直控（新手绕过）
`/vel_cmd` geometry_msgs/Twist：linear.x [0.3,1.2]/[-0.3,-1.2]，angular.z ±[0.5,3.0]，**死区 0.3**，10-20Hz 持续投递。

### SLAM
```bash
# 建图 / 存图 / 加载定位
ros2 service call /slam/set_slam_mode slam_msgs/srv/SetSlamMode "{mode: 2, map_name: '', request_reloc: false}"
ros2 service call /slam/set_slam_mode slam_msgs/srv/SetSlamMode "{mode: 2, map_name: 'blind_home_1f', request_reloc: false}"
ros2 service call /slam/set_slam_mode slam_msgs/srv/SetSlamMode "{mode: 3, map_name: 'blind_home_1f', request_reloc: true}"
# 确认：/slam/status → operation_mode=3, loc_status=3(TRACKING), map_status=4(READY)
```

### 反馈
- `/set_speak`（SetSpeak）：`mode=1` HUMAN_VOICE + `human_language_text`；`mode=0` 预置音 + machine_language_name；`target_state` 0 停 1 播
- `/volume_control`；`/display_node/play_emotion`（mode=**资源 ID**，如 48=say_hi，duration_ms=-1）；`/light_node/control|gradient`、`/light/ear_modulation`
- `/rcp/execute_task`（aorta_msgs/action/ExecuteTask）：DAG 编排语音+灯+头朝向；feedback 有 progress_pct；**RCP 占用资源时别直接 service 覆盖**

### 语音控制面
`speech_msgs/srv/SpeechControl`：命令词模式 0 off/1 本地/2 云(doubao)；闲聊/连续识别开关。`speech_msgs/srv/CommandWord`：在线命令词带槽位。

## 4. 决策层（主动交互）

`SetProactive`(service)：**动作采样池配置，不是感知触发器**；`ContextWatch`(action) 监听上下文键；`GetContext/SetContext`；`ContextEvent`(topic)；`SetRuleSwitches`。
"看到 X 主动播报" = 自己订阅感知 → 规则 → `/set_speak`，官方无成品。

## 5. 安全事实表

| 项 | 事实 | 设计含义 |
|---|---|---|
| 急停 | Passive `/sm/action/lowlevel mode 0`，**会卸力** | 扶鞍时急停=先语音+减速停，Passive 仅最后手段 |
| 速度 | OTA 限 4km/h；vel_cmd 死区 0.3 | SetFollowSpeed 压步行速并写死上限 |
| 负载 | 额定 5kg / 极限 12kg / 稳态拖拽 50kg | 导盲鞍计入 5kg |
| 安装 | 背 4×M4 深 6，孔距 115.5×89；官方转接板 STEP 在 vbot-lab | 鞍具改型有图纸 |
| 避障 | **官方无禁行区/避障开放接口** | 近场保护自己做（腹部红外+双目） |
| 电量 | `/bms_state` soc | 低电语音提前告知+拒绝出发 |
| 配额 | 开发者 1 核 + 4GB | 重计算上伴随电脑 |
| 无线 | 论坛多例不可靠 | 有线为主 |
