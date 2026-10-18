# 最后一米守护者 / Guardian Dog

本项目是一个不连接真实机器人、不发送真实通知的本地演示系统。默认使用 `MockRobot`，支持任务状态机、SQLite 事件持久化、急停、确认门禁和 WebSocket 看板。MuJoCo 后端仅用于经过验证的本地物理实验，当前未验证步态时不会开放行走。

## 启动

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
make frontend-install
make dev
```

打开 <http://localhost:5173>。只运行后端时可访问 <http://localhost:8000>，生产式静态看板需要先执行 `make frontend-build`，然后由 FastAPI 挂载 `frontend/dist`。

## 演示流程

1. 点击“帮我联系家人”，观察任务停留在“待确认”；点击确认后只记录 `delivery_status=simulated`。
2. 发送“去客厅”，观察任务排队、执行和完成。
3. 执行长任务期间点击“紧急停止”，任务被取消并进入停止锁存；点击“恢复模拟”后才允许新任务。
4. 设置 `MOCK_SCENARIO=obstacle`、`disconnect` 或 `timeout`，重启后端即可演示不同故障事件。

## 测试

```bash
make test
make frontend-build
cd frontend && npm test
```

配置见 `.env.example`。物理后端需要额外安装 MuJoCo 并准备指定的 Menagerie Go2 模型；它不会连接 Vbot、CAN 或任何真实通知服务。

MuJoCo 启动：

```bash
.venv/bin/pip install -e '.[simulation,test]'
bash scripts/fetch_model.sh
ROBOT_BACKEND=mujoco make dev
```

可添加 `MUJOCO_VIEWER=1` 打开原生窗口（需本机图形环境）。环境变量通过 shell 设置；若复制 `.env.example` 为 `.env`，可用 `set -a; . ./.env; set +a` 导出后再启动。模型固定来源和步态限制见 [控制器调查](docs/controllers.md)，操作步骤见 [演示脚本](docs/demo-script.md)。
