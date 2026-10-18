# 控制器调查（2026-10-18）

当前进入基础物理阶段，无已验证的步态控制器，不声称可行走。

| 来源 | 检查结果 | 决策 |
| --- | --- | --- |
| [Menagerie Go2](https://github.com/google-deepmind/mujoco_menagerie/tree/main/unitree_go2) | 自由基座、12 电机、home keyframe | 复用模型；MJCF 不是步态 |
| [unitree_mujoco](https://github.com/unitreerobotics/unitree_mujoco) | SDK2/DDS 低层桥，站立/趴下例程 | 不提供可直接调用的高层步行服务，不引入硬件网络桥 |
| [unitree_rl_gym](https://github.com/unitreerobotics/unitree_rl_gym/blob/main/deploy/deploy_mujoco/deploy_mujoco.py) | TorchScript 策略、PD、关节/重力/历史动作/相位观测 | 需要匹配权重与配置；main 的 configs/go2.yaml 探查为 404，尚无验证组合 |
| [MuJoCo MPC Go2](https://github.com/johnzhang3/mujoco_mpc_deploy) | README 指向 go2 分支与独立 MPC 构建 | 候选，未构建验证 |

后续优先复用既有控制器，要求固定提交/许可/权重哈希、确认关节顺序/观测/频率/力矩限幅，验证站立、前进、转向、停止、近障制动。只允许控制 ctrl，世界位姿由 mj_step 积分。
当前零力矩被动物理不是步态，不保证站姿。停止撤销任务、锁存零驱动，但重力惯性继续；不清零 qvel 或冻结世界。

模型固定提交：62ab00f2bc44f6f20c1c9b7a13f3fb1dd41a4e35。模型许可证以 unitree_go2/LICENSE 为准。Go2 仅为 Vbot 仿真替代模型，不代表 Vbot 动力学或接口。
