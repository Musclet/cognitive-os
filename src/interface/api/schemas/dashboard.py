from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DashboardResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    today: str
    weekday: str
    deadline_pressure: dict[str, Any] = Field(default_factory=dict)
    workload_density: dict[str, Any] = Field(default_factory=dict)
    active_context: dict[str, Any] = Field(default_factory=dict)
    homework: list[dict[str, Any]] = Field(default_factory=list)
    homework_count: int = 0
    homework_hidden_count: int = 0
    today_schedule: list[dict[str, Any]] = Field(default_factory=list)
    calendar_events: list[dict[str, Any]] = Field(default_factory=list)
    temporal_blocks: list[dict[str, Any]] = Field(default_factory=list)
    vocab_progress: dict[str, Any] = Field(default_factory=dict)
    fitness: dict[str, Any] = Field(default_factory=dict)
    finance: dict[str, Any] = Field(default_factory=dict)
    parent_funds: dict[str, Any] = Field(default_factory=dict)
    partner_debts: dict[str, Any] = Field(default_factory=dict)
    art: dict[str, Any] = Field(default_factory=dict)
    sync_health: dict[str, Any] = Field(default_factory=dict)
    calendar_consistency: dict[str, Any] = Field(default_factory=dict)
