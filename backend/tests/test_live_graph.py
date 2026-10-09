"""Talks to the real demo tenant. Run with: uv run pytest -m live"""

import pytest

from tech.config import Settings
from tech.graph.client import graph_from_settings

pytestmark = pytest.mark.live


async def test_app_only_token_reads_the_seeded_tenant():
    graph = graph_from_settings(Settings.load())
    try:
        org = (await graph.get("/organization"))["value"][0]
        assert org["displayName"] == "Northwind Parking"
        marcus = await graph.first("/users", {"$filter": "startswith(userPrincipalName,'marcus@')", "$select": "id"})
        assert marcus is not None
        groups = [g async for g in graph.list(f"/users/{marcus['id']}/memberOf", {"$select": "displayName"})]
        assert {"RDS-Users", "VPN-Users"} <= {g.get("displayName") for g in groups}
    finally:
        await graph.aclose()
