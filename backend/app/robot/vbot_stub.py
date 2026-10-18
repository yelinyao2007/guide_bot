from .base import RobotAdapter, RobotError


class VbotAdapter(RobotAdapter):
    """Reserved only. No socket, serial, SDK or CAN side effects."""
    def get_status(self):
        raise RobotError("HARDWARE_UNAVAILABLE", "Vbot 厂商接口尚未验证")

    async def execute(self, action, task_id):
        raise RobotError("HARDWARE_UNAVAILABLE")

    async def stop(self):
        raise RobotError("HARDWARE_UNAVAILABLE")

    async def cancel(self, task_id):
        raise RobotError("HARDWARE_UNAVAILABLE")
