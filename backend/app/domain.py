from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field


def now():
    return datetime.now(timezone.utc).isoformat()


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["request_help", "move_to", "accompany", "stop", "status", "wake", "follow_me", "shake_hands", "start_mapping", "unknown"]
    destination: Literal["living_room", "bedroom", "entrance"] | None = None
    confidence: float = Field(default=1, ge=0, le=1)
    requires_confirmation: bool = False
    reply: str = ""


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    intent: Intent
    status: Literal["created", "awaiting_confirmation", "queued", "running", "succeeded", "failed", "cancelled", "timed_out"] = "created"
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    error_code: str | None = None
    result: dict | None = None


TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
TRANSITIONS = {
    "created": {"awaiting_confirmation", "queued", "failed"},
    "awaiting_confirmation": {"queued", "cancelled", "timed_out"},
    "queued": {"running", "cancelled", "failed"},
    "running": TERMINAL,
}
