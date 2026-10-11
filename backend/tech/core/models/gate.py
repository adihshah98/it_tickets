"""The policy gate's per-step decision (Tech.MD §8)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tech.core.models.base import RiskClass


class GateDecision(BaseModel):
    step_id: str
    action: str
    decision: Literal["auto", "approve", "block"]
    rule_id: str
    reason: str
    risk_class: RiskClass | None = Field(None, description="From the catalogue, never from the plan")
