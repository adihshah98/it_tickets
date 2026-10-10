"""Talks to the real demo tenant. Run with: uv run pytest -m live"""

import pytest

from tech.core.config import Settings
from tech.graph.client import graph_from_settings
from tech.graph.reads import Transcript, run_read

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


async def test_onboarding_reads_ground_marcus_groups_and_license():
    graph = graph_from_settings(Settings.load())
    transcript = Transcript()
    try:
        [marcus] = await run_read(graph, transcript, "users.search", name="Marcus")
        user = await run_read(graph, transcript, "users.get", user_id=marcus["id"])
        assert user["manager"]["userPrincipalName"].startswith("tgarcia@")
        groups = await run_read(graph, transcript, "users.memberOf", user_id=marcus["id"])
        kinds = {g["displayName"]: g["kind"] for g in groups}
        assert kinds["Accounting"] == "m365" and kinds["RDS-Users"] == "security"
        [license] = await run_read(graph, transcript, "users.licenseDetails", user_id=marcus["id"])
        pool = await run_read(graph, transcript, "subscribedSkus.list")
        assert license["skuId"] in {s["skuId"] for s in pool}
        assert all(transcript.observed(g["id"]) for g in groups) and transcript.observed(license["skuId"])
    finally:
        await graph.aclose()
