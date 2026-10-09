import asyncio
from contextlib import aclosing

import pytest

from tech.store import RunState, Store


@pytest.fixture
def store():
    return Store("sqlite://")


@pytest.fixture
def run(store):
    ticket = store.create_ticket("new hire Priya Nair", "tgarcia@example.onmicrosoft.com")
    return store.create_run(ticket.id)


def test_new_run_starts_received_with_a_state_event(store, run):
    assert store.get_run(run.id).state == RunState.RECEIVED
    [event] = store.events(run.id)
    assert (event.seq, event.type, event.payload) == (1, "state", {"from": None, "to": "RECEIVED"})


def test_events_are_numbered_in_append_order(store, run):
    for i in range(5):
        store.append_event(run.id, "finding", {"text": f"f{i}"})
    events = store.events(run.id)
    assert [e.seq for e in events] == [1, 2, 3, 4, 5, 6]
    assert [e.payload.get("text") for e in events[1:]] == ["f0", "f1", "f2", "f3", "f4"]


def test_events_after_seq(store, run):
    store.append_event(run.id, "finding", {"text": "a"})
    store.append_event(run.id, "finding", {"text": "b"})
    assert [e.payload["text"] for e in store.events(run.id, after_seq=2)] == ["b"]


def test_runs_have_independent_sequences(store, run):
    other = store.create_run(store.create_ticket("other", "x@y").id)
    store.append_event(run.id, "finding", {"text": "a"})
    store.append_event(other.id, "finding", {"text": "b"})
    assert [e.seq for e in store.events(other.id)] == [1, 2]
    assert [e.seq for e in store.events(run.id)] == [1, 2]


def test_set_state_persists_and_records_transition(store, run):
    store.set_state(run.id, RunState.TRIAGED)
    store.set_state(run.id, RunState.INVESTIGATING)
    assert store.get_run(run.id).state == RunState.INVESTIGATING
    transitions = [(e.payload["from"], e.payload["to"]) for e in store.events(run.id) if e.type == "state"]
    assert transitions == [(None, "RECEIVED"), ("RECEIVED", "TRIAGED"), ("TRIAGED", "INVESTIGATING")]


def test_unknown_event_type_rejected(store, run):
    with pytest.raises(ValueError):
        store.append_event(run.id, "made_up", {})


def test_non_json_payload_rejected_and_not_stored(store, run):
    with pytest.raises(TypeError):
        store.append_event(run.id, "finding", {"obj": object()})
    assert len(store.events(run.id)) == 1


def test_events_survive_a_new_store_on_the_same_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'tech.db'}"
    s1 = Store(url)
    run = s1.create_run(s1.create_ticket("t", "a@b").id)
    s1.append_event(run.id, "finding", {"text": "kept"})
    s2 = Store(url)
    assert [e.type for e in s2.events(run.id)] == ["state", "finding"]


async def test_stream_replays_history_then_goes_live_without_gaps_or_duplicates(store, run):
    store.append_event(run.id, "finding", {"text": "before"})
    received = []

    async def consume():
        async for event in store.stream(run.id):
            received.append(event.seq)
            if len(received) == 4:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let the consumer subscribe and replay
    store.append_event(run.id, "finding", {"text": "live 1"})
    store.append_event(run.id, "finding", {"text": "live 2"})
    await asyncio.wait_for(task, timeout=2)
    assert received == [1, 2, 3, 4]


async def test_stream_resumes_after_seq(store, run):
    for t in ("a", "b", "c"):
        store.append_event(run.id, "finding", {"text": t})
    got = []

    async def consume():
        async for event in store.stream(run.id, after_seq=2):
            got.append(event.payload["text"])
            if len(got) == 2:
                return

    await asyncio.wait_for(consume(), timeout=2)
    assert got == ["b", "c"]


async def test_unsubscribes_when_consumer_stops(store, run):
    # Consumers (the SSE endpoint) must close the stream; `aclosing` runs its cleanup now, not at GC.
    async def consume_one():
        async with aclosing(store.stream(run.id)) as events:
            async for _ in events:
                return

    await asyncio.wait_for(consume_one(), timeout=2)
    assert store.bus.subscriber_count(run.id) == 0
