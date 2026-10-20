# guide_bot 多会话协作守则（2026-10-21 用户指示）

本目录有多个 Claude 会话并行开发，曾发生整目录覆盖回滚他人补丁的事故。**动手前必读：**

1. **唯一工作副本 = 本 VM 的 ~/guide_bot**。改任何文件前先拉最新（scp/rsync 回本地或直接在 VM 上改）。
2. **禁止把本地整目录推回 VM 覆盖**——只允许增量补丁/合并单个文件。
3. 以下改动已部署且必须保留（覆盖即事故）：
   - backend/app/dify_client.py（Dify workflow 客户端，新增文件）
   - backend/app/vbot_config.py：dify_enabled/dify_url/dify_api_key/dify_timeout/owner_name 字段 + mask_key/public/merge_incoming
   - backend/app/main.py：/api/vbot/dify-test 端点 + ROBOT_BACKEND=vbot 接线（VbotEduAdapter）
   - backend/app/agent.py：dify_resolve（Dify 优先，失败降级本地规则；急停不走 Dify）
   - backend/app/service.py：异步 resolver 支持 + 新意图(find_seat/nearby_fun/record_field_save/passthrough)识别不执行
   - backend/app/domain.py：Intent 新枚举 + slots/speak_text/raw_text 字段
   - frontend/src/main.tsx：真机配置面板内「Dify 意图识别」区块（含测试按钮）
   - scripts/start_all_vm.sh：默认 ROBOT_BACKEND=vbot 且不启 MuJoCo；仿真仅 --with-sim（用户要求 MuJoCo 默认关闭）
4. 改后端后重启：bash ~/guide_bot/scripts/start_all_vm.sh（前端需在外部机器构建后推 dist，VM 无 node）。
5. 真机适配器在 backend/app/robot/vbot_adapter.py（VbotEduAdapter）；狗未连线时看板显示未连接属正常。

## Dify 栈运维要点（2026-10-21 实测核实）
- Dify 1.17.1 在 ~/dify（docker compose，session ros2-16 部署）；nginx :80 与本项目 :8000 共存
- **~/dify/docker/docker-compose.override.yaml 是上游 local_sandbox 参数 bug 的修复，重启/更新 dify 栈勿删**
- 重启：cd ~/dify/docker && docker compose up -d
- 管理台**已初始化**（setup_at 2026-10-20T13:22:33，存于 DB volume，容器重建不丢）；登录 http://192.168.64.2 或 VM 内 localhost
- guide_bot 后端调 Dify 用 dify_url=http://127.0.0.1/v1/workflows/run（同机回环）
- 容器常态：带 healthcheck 的 5 个显示 (healthy)；nginx/web/worker/plugin_daemon/weaviate/ssrf 等 compose 未配 healthcheck 只显示 Up；init_permissions Exited(0) 是一次性任务——均非故障

## 进行中变更（动手前先核实是否已完成）
- 2026-10-21 凌晨：session ros2-78 文件级合并 base.py / service.py / main.py / simvbot_adapter.py / tests，并删 mock.py、vbot_stub.py（用户指示）。完成信号 = 其 SendMessage 通知 + pytest 通过 + start_all_vm.sh 重启成功。在此之前不要推这几个文件。dify_* 文件、agent.py、domain.py、vbot_config.py、frontend、scripts 不在其范围。
- ros2-58 责任范围：simulation/*（run_sim/dog_viewer/scene_tools/fetch_assets/gen_dog_xml）、frontend SimPanel 段落、.claude/skills/vbot-dog/*；改这些文件前先 ping 或拉 VM 最新。其历史提交在 GitHub 分支「配置页草稿」，VM 为唯一工作副本
- 2026-10-21 凌晨运行态：ros2-58 应用户"怎么还是 mock"提问以 --with-sim 重启（backend=simvbot + MuJoCo :8001）。**看板默认模式（vbot/simvbot/自动）待用户裁决；裁决前任何会话不得 kill sim 或擅自重启后端**
- ros2-78 合并（base/service/main/simvbot_adapter/tests + 删 mock/vbot_stub）进行中，重启挂起等模式裁决

## 裁决（用户，2026-10-21）
- 看板默认模式 = **vbot 真机优先**：start_all_vm.sh 默认不启 MuJoCo；仿真仅 --with-sim 按需，由 ros2-58（或需求方）启动并事先知会
- 裁决重启由 ros2-78 执行（其合并已推送磁盘：base/service/main/simvbot_adapter/tests + 删 mock/vbot_stub）；ros2-58 的 sim 随裁决重启停止
- run_sim /state HTTP 字段为 ros2-58 与 simvbot_adapter/SimPanel 的兼容契约（清单见会话记录），增删字段先 ping ros2-58
- Dify 管理员凭据用户自行掌握（2026-10-20 13:22 初始化），自行登录建 workflow；无需重置
