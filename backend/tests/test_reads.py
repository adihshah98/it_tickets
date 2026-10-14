import asyncio

import httpx
import pytest
import respx

from tech.graph.client import GRAPH, GraphClient, GraphError
from tech.graph.reads import Transcript, classify, run_read

MARCUS = "8a590663-09f2-4ee8-ab1a-6195ca3943f9"
TAYLOR = "11111111-2222-3333-4444-555555555555"
ACCOUNTING = "A55F62F7-A1FE-4314-8D61-88D40F8CFD54"
SPB = "cbdc14ab-d96c-4c30-b9f4-6ada7cdc1d46"


async def tokens() -> tuple[str, float]:
    return "tok", 9e12


async def no_sleep(_: float) -> None:
    pass


@pytest.fixture
def graph():
    return GraphClient(tokens, sleep=no_sleep)


@pytest.fixture
def transcript():
    return Transcript()


def page(*items: dict) -> httpx.Response:
    return httpx.Response(200, json={"value": list(items)})


@pytest.mark.parametrize(("group", "kind"), [
    ({"groupTypes": ["Unified"], "mailEnabled": True, "securityEnabled": False}, "m365"),
    ({"groupTypes": [], "mailEnabled": False, "securityEnabled": True}, "security"),
    ({"groupTypes": [], "mailEnabled": True, "securityEnabled": True}, "mail_security"),
    ({"groupTypes": [], "mailEnabled": True, "securityEnabled": False}, "distribution_list"),
])
def test_classify(group, kind):
    assert classify(group) == kind


@respx.mock
async def test_member_of_trims_classifies_and_records_observed_ids(graph, transcript):
    respx.get(f"{GRAPH}/users/{MARCUS}/memberOf").mock(return_value=page(
        {"@odata.type": "#microsoft.graph.group", "id": ACCOUNTING, "displayName": "Accounting",
         "groupTypes": ["Unified"], "mailEnabled": True, "securityEnabled": False, "mail": "acc@x"},
        {"@odata.type": "#microsoft.graph.directoryRole", "id": "99999999-0000-0000-0000-000000000000"},
        {"@odata.type": "#microsoft.graph.group", "id": TAYLOR, "displayName": "IT Admins", "groupTypes": [],
         "mailEnabled": False, "securityEnabled": True, "isAssignableToRole": True},
    ))
    groups = await run_read(graph, transcript, "users.memberOf", user_id=MARCUS)
    assert groups[0] == {"id": ACCOUNTING, "displayName": "Accounting", "kind": "m365", "mail": "acc@x",
                         "roleAssignable": False}
    # IDs are compared lowercase; the directory role was trimmed away, so it was never observed
    assert transcript.observed(ACCOUNTING.lower()) and transcript.observed(ACCOUNTING)
    assert not transcript.observed("99999999-0000-0000-0000-000000000000")
    assert transcript.called("users.memberOf")
    # what was seen about each group, for the linter and gate
    assert transcript.object(ACCOUNTING) == {"displayName": "Accounting", "kind": "m365", "roleAssignable": False}
    assert transcript.object(TAYLOR)["roleAssignable"] is True
    assert transcript.object("99999999-0000-0000-0000-000000000000") == {}


def test_transcript_collects_skus_and_nested_objects(transcript):
    transcript.record("users.get", {}, {"id": MARCUS, "displayName": "Marcus Webb",
                                        "manager": {"id": TAYLOR, "displayName": "Taylor Garcia"}})
    transcript.record("subscribedSkus.list", {}, [{"skuId": SPB, "skuPartNumber": "SPB", "available": 0}])
    assert transcript.object(TAYLOR) == {"displayName": "Taylor Garcia"}
    assert transcript.object(SPB) == {"skuPartNumber": "SPB"}


@respx.mock
async def test_args_are_not_observations(graph, transcript):
    respx.get(f"{GRAPH}/users/{MARCUS}/licenseDetails").mock(return_value=page())
    await run_read(graph, transcript, "users.licenseDetails", user_id=MARCUS)
    assert not transcript.observed(MARCUS)  # an ID the model passed in isn't grounded by an empty result


@respx.mock
async def test_users_get_reads_sign_in_activity_through_a_filter_query(graph, transcript):
    respx.get(f"{GRAPH}/users/{MARCUS}").mock(return_value=httpx.Response(200, json={"id": MARCUS}))
    activity = respx.get(f"{GRAPH}/users", params={"$select": "id,signInActivity"}).mock(return_value=page(
        {"id": MARCUS, "signInActivity": {"lastSignInDateTime": "2026-10-08T09:00:00Z", "lastSignInRequestId": "r"}},
    ))
    respx.get(f"{GRAPH}/users/{MARCUS}/manager").mock(return_value=httpx.Response(200, json={
        "id": TAYLOR, "displayName": "Taylor Garcia", "userPrincipalName": "tgarcia@x", "jobTitle": "x"}))
    user = await run_read(graph, transcript, "users.get", user_id=MARCUS)
    assert activity.calls.last.request.url.params["$filter"] == f"id eq '{MARCUS}'"
    assert user["signInActivity"] == {"lastSignInDateTime": "2026-10-08T09:00:00Z",
                                      "lastNonInteractiveSignInDateTime": None, "lastSuccessfulSignInDateTime": None}
    assert user["manager"] == {"id": TAYLOR, "displayName": "Taylor Garcia", "userPrincipalName": "tgarcia@x"}
    assert transcript.observed(TAYLOR)


@respx.mock
async def test_users_get_without_manager_or_sign_in_permission(graph, transcript):
    respx.get(f"{GRAPH}/users/{MARCUS}").mock(return_value=httpx.Response(200, json={"id": MARCUS}))
    respx.get(f"{GRAPH}/users").mock(return_value=httpx.Response(403, json={"error": {"code": "Forbidden"}}))
    respx.get(f"{GRAPH}/users/{MARCUS}/manager").mock(
        return_value=httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}}))
    user = await run_read(graph, transcript, "users.get", user_id=MARCUS)
    assert user == {"id": MARCUS, "signInActivity": None, "manager": None}


@respx.mock
async def test_users_get_with_no_recorded_sign_in(graph, transcript):
    respx.get(f"{GRAPH}/users/{MARCUS}").mock(return_value=httpx.Response(200, json={"id": MARCUS}))
    respx.get(f"{GRAPH}/users").mock(return_value=page({"id": MARCUS}))  # Graph omits signInActivity
    respx.get(f"{GRAPH}/users/{MARCUS}/manager").mock(return_value=httpx.Response(404, json={"error": {}}))
    user = await run_read(graph, transcript, "users.get", user_id=MARCUS)
    assert user["signInActivity"] == {"lastSignInDateTime": None, "lastNonInteractiveSignInDateTime": None,
                                      "lastSuccessfulSignInDateTime": None}


@respx.mock
async def test_subscribed_skus_computes_available_seats(graph, transcript):
    respx.get(f"{GRAPH}/subscribedSkus").mock(return_value=page(
        {"skuId": SPB, "skuPartNumber": "SPB", "prepaidUnits": {"enabled": 25}, "consumedUnits": 25, "extra": 1},
    ))
    assert await run_read(graph, transcript, "subscribedSkus.list") == [
        {"skuId": SPB, "skuPartNumber": "SPB", "enabled": 25, "consumed": 25, "available": 0},
    ]
    assert transcript.observed(SPB)


@respx.mock
async def test_users_list_is_newest_first(graph, transcript):
    respx.get(f"{GRAPH}/users").mock(return_value=page(
        {"id": "a", "createdDateTime": "2024-01-01T00:00:00Z"},
        {"id": "b", "createdDateTime": "2026-09-01T00:00:00Z"},
    ))
    assert [u["id"] for u in await run_read(graph, transcript, "users.list")] == ["b", "a"]


@respx.mock
async def test_users_search_escapes_quotes(graph, transcript):
    route = respx.get(f"{GRAPH}/users").mock(return_value=page())
    await run_read(graph, transcript, "users.search", name="O'Brien")
    assert "startswith(displayName,'O''Brien')" in route.calls.last.request.url.params["$filter"]


@respx.mock
async def test_files_search_skips_groups_without_a_site(graph, transcript):
    respx.get(f"{GRAPH}/groups").mock(return_value=page({"id": "g1", "displayName": "Ops"},
                                                        {"id": "g2", "displayName": "New"}))
    respx.get(f"{GRAPH}/groups/g1/drive").mock(return_value=httpx.Response(200, json={"id": "d1"}))
    respx.get(f"{GRAPH}/groups/g2/drive").mock(return_value=httpx.Response(404, json={"error": {}}))
    respx.get(url__startswith=f"{GRAPH}/drives/d1/root/search").mock(return_value=page(
        {"id": "i1", "name": "rates.xlsx", "lastModifiedDateTime": "2026-10-01T00:00:00Z"}))
    [hit] = await run_read(graph, transcript, "files.search", query="rates")
    assert hit == {"site": "Ops", "drive_id": "d1", "item_id": "i1", "name": "rates.xlsx",
                   "lastModified": "2026-10-01T00:00:00Z"}


@respx.mock
async def test_files_search_falls_back_to_the_top_folder_while_the_index_lags(graph, transcript):
    respx.get(f"{GRAPH}/groups").mock(return_value=page({"id": "g1", "displayName": "Ops"}))
    respx.get(f"{GRAPH}/groups/g1/drive").mock(return_value=httpx.Response(200, json={"id": "d1"}))
    respx.get(url__startswith=f"{GRAPH}/drives/d1/root/search").mock(return_value=page())
    respx.get(f"{GRAPH}/drives/d1/root/children").mock(return_value=page(
        {"id": "i1", "name": "Q3 Parking Rates.xlsx", "lastModifiedDateTime": "t", "file": {}},
        {"id": "i2", "name": "Q3 Parking Rates", "lastModifiedDateTime": "t", "folder": {}},  # a folder
        {"id": "i3", "name": "Q2 Parking Rates.xlsx", "lastModifiedDateTime": "t", "file": {}}))
    hits = await run_read(graph, transcript, "files.search", query="q3 parking rates")
    assert [h["item_id"] for h in hits] == ["i1"]


@respx.mock
async def test_files_search_runs_at_most_two_drive_searches_at_once(graph, transcript):
    """Live (Build-Day21 part 7): six concurrent root/search calls took 37 s (three stalled ~36 s each); two at a
    time took 4 s."""
    groups = [{"id": f"g{i}", "displayName": f"Site {i}"} for i in range(6)]
    respx.get(f"{GRAPH}/groups").mock(return_value=page(*groups))
    for i in range(6):
        respx.get(f"{GRAPH}/groups/g{i}/drive").mock(return_value=httpx.Response(200, json={"id": f"d{i}"}))
        respx.get(f"{GRAPH}/drives/d{i}/root/children").mock(return_value=page())
    running, peak = 0, 0

    async def search(request):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return page({"id": "i", "name": "rates.xlsx", "lastModifiedDateTime": "t"})

    respx.get(url__regex=rf"{GRAPH}/drives/d\d/root/search.*").mock(side_effect=search)
    hits = await run_read(graph, transcript, "files.search", query="rates")
    assert len(hits) == 6 and peak == 2


async def test_the_transcript_grounds_files_by_search_and_versions(transcript):
    transcript.record("files.search", {"query": "rates"}, [{"drive_id": "b!d", "item_id": "01A", "name": "x"}])
    assert transcript.files == {("b!d", "01A"): None}  # found; its history not read yet
    transcript.record("files.versions", {"drive_id": "b!d", "item_id": "01A"}, [{"id": "2.0"}, {"id": "1.0"}])
    assert transcript.files == {("b!d", "01A"): ["2.0", "1.0"]}
    transcript.record("files.search", {"query": "rates"}, [{"drive_id": "b!d", "item_id": "01A", "name": "x"}])
    assert transcript.files == {("b!d", "01A"): ["2.0", "1.0"]}  # a second search doesn't forget the history


@respx.mock
async def test_risky_user_flagged(graph, transcript):
    respx.get(f"{GRAPH}/identityProtection/riskyUsers/{MARCUS}").mock(return_value=httpx.Response(200, json={
        "id": MARCUS, "riskLevel": "high", "riskState": "atRisk", "riskDetail": "none",
        "riskLastUpdatedDateTime": "2026-10-09T21:20:00Z", "userPrincipalName": "marcus@x"}))
    assert await run_read(graph, transcript, "riskyUsers.get", user_id=MARCUS) == {
        "risky": True, "riskLevel": "high", "riskState": "atRisk", "riskDetail": "none",
        "riskLastUpdatedDateTime": "2026-10-09T21:20:00Z"}


@respx.mock
async def test_risky_user_remediated_is_not_risky(graph, transcript):
    respx.get(f"{GRAPH}/identityProtection/riskyUsers/{MARCUS}").mock(return_value=httpx.Response(200, json={
        "riskLevel": "none", "riskState": "remediated"}))
    assert (await run_read(graph, transcript, "riskyUsers.get", user_id=MARCUS))["risky"] is False


@respx.mock
async def test_risky_user_never_flagged(graph, transcript):
    respx.get(f"{GRAPH}/identityProtection/riskyUsers/{MARCUS}").mock(
        return_value=httpx.Response(404, json={"error": {"code": "ResourceNotFound"}}))
    assert await run_read(graph, transcript, "riskyUsers.get", user_id=MARCUS) == {"risky": False}


@respx.mock
async def test_risky_user_unavailable_is_unknown_not_clean(graph, transcript):
    respx.get(f"{GRAPH}/identityProtection/riskyUsers/{MARCUS}").mock(
        return_value=httpx.Response(403, json={"error": {"code": "Authorization_RequestDenied", "message": "no"}}))
    result = await run_read(graph, transcript, "riskyUsers.get", user_id=MARCUS)
    assert result["risky"] is None and "Authorization_RequestDenied" in result["detail"]
    assert transcript.called("riskyUsers.get")  # recorded as a successful read the model can reason over


@respx.mock
async def test_graph_errors_are_recorded_then_raised(graph, transcript):
    respx.get(f"{GRAPH}/auditLogs/signIns").mock(
        return_value=httpx.Response(403, json={"error": {"code": "Authorization_RequestDenied", "message": "no"}}))
    with pytest.raises(GraphError):
        await run_read(graph, transcript, "signIns.list", user_id=MARCUS)
    [call] = transcript.calls
    assert call.tool == "signIns.list" and call.error.startswith("403")
    assert not transcript.called("signIns.list")


async def test_files_search_finds_a_fresh_upload_before_the_index_does(tenant, graph, transcript):
    site = tenant.add_group("0c0c0c0c-0000-4000-8000-000000000001", "Northwind Operations", "m365")
    tenant.add_file(tenant.add_drive(site), "Q3 Parking Rates.xlsx", [("2026-10-13T09:05:00Z", 12606)])
    tenant.search_lag = 1
    [hit] = await run_read(graph, transcript, "files.search", query="Q3 Parking Rates")
    assert hit["name"] == "Q3 Parking Rates.xlsx" and hit["drive_id"].startswith("b!")
