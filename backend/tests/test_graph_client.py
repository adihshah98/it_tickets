import httpx
import pytest
import respx

from tech.graph.client import GRAPH, GraphClient, GraphError, GraphTransportError, odata_str


class FakeTokens:
    """Token provider that counts calls and can hand out a near-expired token first."""

    def __init__(self, ttl: float = 3600):
        self.calls = 0
        self.ttl = ttl

    async def __call__(self) -> tuple[str, float]:
        import time

        self.calls += 1
        return f"tok{self.calls}", time.time() + self.ttl


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(sleeps):
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return GraphClient(FakeTokens(), sleep=fake_sleep)


def err(status: int, code: str = "x", message: str = "boom", headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}}, headers=headers)


@respx.mock
async def test_get_returns_json_and_sends_bearer(client):
    route = respx.get(f"{GRAPH}/organization").mock(return_value=httpx.Response(200, json={"value": [{"id": "o1"}]}))
    assert await client.get("/organization") == {"value": [{"id": "o1"}]}
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok1"


@respx.mock
async def test_429_honours_retry_after(client, sleeps):
    respx.get(f"{GRAPH}/users").mock(side_effect=[err(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})])
    assert await client.get("/users") == {}
    assert sleeps == [7.0]


@respx.mock
async def test_5xx_gives_up_after_three_tries(client, sleeps):
    route = respx.get(f"{GRAPH}/users").mock(return_value=err(503))
    with pytest.raises(GraphError) as exc:
        await client.get("/users")
    assert exc.value.status == 503
    assert route.call_count == 3
    assert len(sleeps) == 2


@respx.mock
async def test_404_is_not_retried_by_default(client, sleeps):
    route = respx.get(f"{GRAPH}/users/u1").mock(return_value=err(404, "Request_ResourceNotFound"))
    with pytest.raises(GraphError) as exc:
        await client.get("/users/u1")
    assert exc.value.status == 404 and exc.value.code == "Request_ResourceNotFound"
    assert route.call_count == 1 and sleeps == []


@respx.mock
async def test_replication_lag_404_retried_when_asked(client, sleeps):
    respx.post(f"{GRAPH}/groups/g1/members/$ref").mock(
        side_effect=[err(404), err(404), httpx.Response(204)]
    )
    assert await client.post("/groups/g1/members/$ref", {"@odata.id": "x"}, retry_replication=True) is None
    assert len(sleeps) == 2 and sleeps[0] < sleeps[1]  # exponential backoff


@respx.mock
async def test_replication_lag_400_does_not_exist_retried(client, sleeps):
    respx.post(f"{GRAPH}/users/u1/assignLicense").mock(side_effect=[
        err(400, "Request_BadRequest",
            "Resource 'u1' does not exist or one of its queried reference-property objects are not present."),
        httpx.Response(200, json={"id": "u1"}),
    ])
    assert await client.post("/users/u1/assignLicense", {}, retry_replication=True) == {"id": "u1"}
    assert len(sleeps) == 1


@respx.mock
async def test_replication_retries_stop_after_five_tries_within_about_a_minute(client, sleeps):
    route = respx.get(f"{GRAPH}/users/u1").mock(return_value=err(404))
    with pytest.raises(GraphError):
        await client.get("/users/u1", retry_replication=True)
    assert route.call_count == 5
    assert 45 <= sum(sleeps) <= 75


@respx.mock
async def test_other_400_and_403_fail_immediately(client, sleeps):
    bad_request = err(400, "Request_BadRequest", "Invalid value for usageLocation")
    respx.patch(f"{GRAPH}/users/u1").mock(return_value=bad_request)
    respx.get(f"{GRAPH}/auditLogs/signIns").mock(return_value=err(403, "Authorization_RequestDenied"))
    with pytest.raises(GraphError):
        await client.patch("/users/u1", {"usageLocation": "XX"}, retry_replication=True)
    with pytest.raises(GraphError) as exc:
        await client.get("/auditLogs/signIns")
    assert exc.value.status == 403 and sleeps == []


@respx.mock
async def test_list_follows_next_link(client):
    route = respx.get(url__startswith=f"{GRAPH}/users").mock(side_effect=[
        httpx.Response(200, json={"value": [{"id": "a"}], "@odata.nextLink": f"{GRAPH}/users?$skiptoken=2"}),
        httpx.Response(200, json={"value": [{"id": "b"}]}),
    ])
    assert [u["id"] async for u in client.list("/users")] == ["a", "b"]
    assert "skiptoken=2" in str(route.calls.last.request.url)


@respx.mock
async def test_first_returns_none_when_empty(client):
    respx.get(f"{GRAPH}/groups").mock(return_value=httpx.Response(200, json={"value": []}))
    assert await client.first("/groups", {"$filter": "mailNickname eq 'x'"}) is None


@respx.mock
async def test_token_refreshed_when_close_to_expiry(sleeps):
    tokens = FakeTokens(ttl=60)  # inside the 5-minute refresh margin

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    c = GraphClient(tokens, sleep=fake_sleep)
    respx.get(f"{GRAPH}/organization").mock(return_value=httpx.Response(200, json={}))
    await c.get("/organization")
    await c.get("/organization")
    assert tokens.calls == 2


@respx.mock
async def test_204_returns_none(client):
    respx.get(f"{GRAPH}/users/u1").mock(return_value=httpx.Response(204))
    assert await client.get("/users/u1") is None


def test_odata_str_escapes_quotes():
    assert odata_str("O'Brien") == "O''Brien"


@respx.mock
async def test_a_read_that_times_out_is_tried_again(client, sleeps):
    respx.get(f"{GRAPH}/auditLogs/signIns").mock(side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json={})])
    assert await client.get("/auditLogs/signIns") == {}
    assert sleeps == [2.0]


@respx.mock
async def test_a_read_that_keeps_timing_out_is_a_graph_error(client):
    respx.get(f"{GRAPH}/auditLogs/signIns").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(GraphError) as caught:  # so every Graph-error handler covers it
        await client.get("/auditLogs/signIns")
    assert isinstance(caught.value, GraphTransportError)
    assert caught.value.status == 0 and caught.value.code == "ReadTimeout"


@respx.mock
async def test_a_write_that_times_out_is_not_repeated(client):
    route = respx.post(f"{GRAPH}/users").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(GraphTransportError, match="may or may not have been applied"):
        await client.post("/users", {"displayName": "x"})
    assert route.call_count == 1
