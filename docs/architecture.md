# 架构与事件契约

React 看板通过 Vite 代理访问 FastAPI；构建后的 `frontend/dist` 可直接由 FastAPI 托管。任务由单一异步队列串行执行，任务变更与对应事件在同一 SQLite 事务写入。事件包含 version、seq、type、timestamp、payload。

WebSocket `/ws/events?after=N` 重放 N 之后的持久事件，前端断线后保留序号并自动重连。任务及机器人状态另由 REST 定期校准。机器人状态以 10 Hz 记录，当前适合本地短期演示；数据库无自动清理策略。

MockRobot 提供确定性动作。MuJoCo 使用 spawn 子进程独立加载模型及执行 mj_step，FastAPI 只读取遥测和操作无锁停止标志。故障或急停锁存后必须显式恢复；物理恢复创建新进程，旧任务不会继续。
