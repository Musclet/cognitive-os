from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkoutSessionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    session: dict[str, Any] | None = None
    date: str
    weekday: str
    planned_day: str
    is_training_day: bool
    available_days: list[str] = Field(default_factory=list)
    recommended_day: str
