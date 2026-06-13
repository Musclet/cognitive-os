from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from src.interface.api.schemas.dashboard import DashboardResponse


class FinanceActionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    message: str
    action: str | None = None
    events: int | None = None
    needs_followup: bool = False
    dashboard: DashboardResponse | None = None
    action_id: str | None = None
    can_undo: bool | None = None


class FinanceRevertResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool
    message: str
    needs_followup: bool = False
    action_id: str | None = None
    action_type: str | None = None
    events: int | None = None
    dashboard: DashboardResponse | None = None
