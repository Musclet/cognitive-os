from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.interface.api.schemas.dashboard import DashboardResponse


class CalendarProposalResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    message: str
    needs_followup: bool = False
    proposal: dict[str, Any] | None = None
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    dashboard: DashboardResponse | None = None
