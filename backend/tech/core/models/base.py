"""Shared primitives for the model contracts: IDs, references, the intent and risk-class literals.

These live in the lowest module of the `core.models` package so triage, params, plan and gate can all
import them without a cycle. ID fields take either an object ID observed during investigation (a GUID)
or a reference to an earlier step's output, written `$ref:<step id>.output.id`.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
GUID_RE = re.compile(GUID)
REF_RE = re.compile(r"^\$ref:([A-Za-z0-9_-]+)\.output\.([A-Za-z]+)$")

ObjectId = Annotated[str, Field(pattern=f"^{GUID}$", description="An object ID you observed in a tool result")]
ObjectIdOrRef = Annotated[str, Field(
    pattern=rf"^({GUID}|\$ref:[A-Za-z0-9_-]+\.output\.id)$",
    description="An object ID you observed in a tool result, or '$ref:<step id>.output.id' for an object an "
                "earlier step creates",
)]
TapRef = Annotated[str, Field(
    pattern=r"^\$ref:[A-Za-z0-9_-]+\.output\.tap$",
    description="'$ref:<tap.create step id>.output.tap'. The TAP value itself is never in a plan",
)]

Intent = Literal["user.onboard", "user.offboard", "mfa.reset", "file.restore_version", "unknown"]
RiskClass = Literal[
    "read", "write.reversible", "access.revoke", "data.access", "security.credential",
    "spend", "destructive.soft", "none",
]


def parse_ref(value: Any) -> tuple[str, str] | None:
    """`$ref:s1.output.id` -> ("s1", "id"); anything else -> None."""
    if isinstance(value, str) and (m := REF_RE.match(value)):
        return m.group(1), m.group(2)
    return None


def alias(*names: str, **kw: Any) -> Any:
    """Accept the Graph name first, then the spellings the model also uses; serialize as the Graph name."""
    return Field(validation_alias=AliasChoices(*names), serialization_alias=names[0], **kw)


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)
