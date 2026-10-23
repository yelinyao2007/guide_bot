# 最后十米 · 小狗找空座（guide_bot）

维他动力大头狗 EDU（VbotBaboEDU / foot_quadruped）的应用层项目：
**语音指令 → Dify 意图识别 → 规则化链路 → 狗的原生能力**（跟随 / SLAM 建图 / 语义导航 / TTS），
外带一块实时看板。核心场景：公共空间里让机器狗帮你找空座位并带你过去——"最后十米"。

## 它能干什么

四句语音暗号（Dify 意图识别；其余话语兜底透传给狗原生智能体，绝不瞎触发）：

| 暗号 | 链路 |
|---|---|
| 跟我开始记录场地 | 跟随 + SLAM 建图 + 存图登记（看板地图下拉可选/加载定位） |
| 检查链接状态 | 链路 + 设备只读自检 → 语音播报（"已链接到<主人称呼>的电脑……"） |
| 去帮我找个位子 | SemanticNavQuery 找空位 → 播报方位 → 引导 → 最后十米确认 → 蹲守 |
| 这附近有什么好玩的地方 | 抓帧 + OmniPrompt 看图说话 → 口语播报 |

- 看板 `:8000`：任务状态机、事件流、真机/Dify 配置面板（含 API Key 打码与「测试 Dify」按钮）、仿真编排面板
- 真机适配器 `VbotEduAdapter`：SSH 通道跑 ros2 CLI（ROS 只在狗上），路由按类型后缀现场发现，
  状态轮询线程缓存，安全停止序列按 EDU 文档（SAFE_STOP/SAFE_LAYDOWN，不用 FIXED_* 顶替）
- 意图解析 `dify_resolve`：Dify workflow 优先，超时/失败静默降级本地规则；**急停永远走本地拦截，不经过任何远程服务**

## 快速开始（VM ros@192.168.64.2）

```bash
bash scripts/start_all_vm.sh                       # 真机模式（默认；不启 MuJoCo）
bash scripts/start_all_vm.sh --with-sim            # 空位仿真 + simvbot 模式（MuJoCo :8001）
bash scripts/start_all_vm.sh --with-sim --viewer   # 再加虚拟机桌面 3D 窗口（需 Xorg 会话）
```

看板地址：`http://<vm-ip>:8000`（Mac 直连 `http://192.168.64.2:8000`）。
狗未连线时看板显示"未连接"属真实状态：插网线 → 看板「自检」→「恢复」解锁。

## 配置

### WSL 真机找空位面板

主看板内的「真机找空位」显示电脑端识别画面、椅子状态，并可点击「找空位并播报」。
浏览器通过 guide_bot 同源 API 访问，无需单独打开识别页面。

当前电脑已部署的识别进程：

```bash
~/vbot-cup/.venv/bin/python ~/vbot-cup/seats.py
```

保持该进程运行，再启动 guide_bot（项目根目录执行）：

```bash
ROBOT_BACKEND=vbot python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

前端先执行 `npm ci && npm run build`（目录 `frontend/`）。浏览器打开
`http://localhost:8000`。识别进程地址默认 `http://127.0.0.1:8766`，
可通过后端环境变量 `SEAT_DETECTOR_URL` 指定其他伴随电脑地址。
接口：`GET /api/seats/status`、`GET /api/seats/frame`、`POST /api/seats/find`。
空位检测提供疑似空椅子与画面方位，座面需现场确认。

- 真机/Dify 配置在看板「真机连接配置」面板，落盘 `vbot_config.json`
  （**含 API Key，已 gitignore**；样例见 `vbot_config.example.json`）
- 建图：在配置页填写地图名并保存，发送「跟我开始记录场地」，牵引机器狗走完场地，再发送「保存地图」。
  真机通过 `/slam/set_slam_mode` 的 `mode=2 + map_name` 保存到狗端 `/app_param/map/{地图名}`。
  之前看到的摄像头画面不能直接恢复地图；下次使用保存的地图时需要设备侧以 `mode=3` 加载并请求重定位。
- Dify 栈在 VM `~/dify`（docker compose，nginx :80）；workflow 提示词与数据格式契约见
  《大头狗Dify语音调度接入方案.md》（ovo 仓 ros2/ 目录）
- 前端构建：VM 无 node，需在外部机器 `npm install && npm run build` 后把
  `frontend/src` 与 `frontend/dist` 推回 VM（dist 已 gitignore）

## 目录

```
backend/     FastAPI + 任务状态机 + robot 适配器（vbot 真机 / simvbot 仿真 / mujoco 物理实验）
frontend/    React 看板（Vite）
simulation/  MuJoCo 空位仿真 + 假感知服务 + 编排 HTTP :8001（--with-sim 时启动）
scripts/     启动脚本、现场案例脚本
docs/        架构、安全、控制器调查、demo 脚本
```

## 安全与纪律

- 急停词本地拦截，最快路径；跟随限速写死 0.5 m/s（散步速度），不可被网页/指令覆盖
- 电量 < 30% 拒绝出发并语音告知；SLAM `loc_status` LOST 即停车 + 语音，不盲重试
- 真机新动作首测离地/扶稳、急停手段在手边；服务响应只代表接纳，观察终态才算完成
- 多会话协作守则见 `CLAUDE.md`（唯一工作副本 = VM、文件级合并、禁止整目录覆盖、裁决机制）

## 相关文档

- 《大头狗Dify语音调度接入方案.md》——Dify 插入点、数据格式契约、四条链路细则、现场核对清单
- 飞书《最后十米 · 小狗找空座 —— 项目说明文档》——项目概述/三步流程/三层架构画板
- 飞书《机器狗和大管家 Dify —— 讲给小朋友的版本》——白话版原理与接入教程
- `.claude/skills/vbot-dog/`——连接自检、接口速查、debug 坑表、空位案例脚本
