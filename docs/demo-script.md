# 演示脚本

默认 `make dev` 后访问 localhost:5173。

1. 点击“帮我联系家人”：应显示待确认，尚无 notification.simulated；点击任务卡中的确认按钮后显示模拟回执。
2. Mock 下点击“去客厅”：排队、执行、完成；MuJoCo 下该指令应明确失败 GAIT_UNAVAILABLE。
3. 点击“陪我去客厅”，执行时急停。再发送动作应显示 STOP_LATCHED。选择“客厅陪伴”并恢复后可提交新任务。
4. 选择“近障停止”并恢复：MuJoCo 立即预防性停止，无行走和制动演示；Mock 在动作执行时报告 OBSTACLE_BLOCKED。
5. 选择“模拟求助”并恢复，重复确认流程。MuJoCo 恢复后的 worker_pid 应改变，旧任务保持终态。

`PYTHONPATH=. .venv/bin/python scripts/demo_smoke.py` 可对正在运行的 Mock 后端验证上述核心 API 流程。
