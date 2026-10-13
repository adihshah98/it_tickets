"""API: tickets, snapshot, approval, run now, the one-time link, and the SSE stream (replay, resume, live, end)."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from conftest import DOMAIN, LEARNED_RESTORE, SPB, TAYLOR, drafter_model, drafts_model
from fake_tenant import TAP_VALUE

from tech import api, runner
from tech.core.store import Link, RunState, utcnow
from tech.execution import links, scheduler

ORIGIN = "http://ui.test"


@pytest.fixture
def app(rt):
    return api.create_app(rt, web_origin=ORIGIN)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as c:
        yield c


async def drained(app):
    await app.state.tasks.drain()


def everything(rt, run_id) -> str:
    return json.dumps([e.payload for e in rt.store.events(run_id)])


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if fields:
            events.append({"id": fields.get("id"), "event": fields.get("event"), "data": json.loads(fields["data"])})
    return events


async def approved_run(app, client, gated, **answers) -> int:
    run_id = gated()
    r = await client.post(f"/runs/{run_id}/approval", json={"by": "tgarcia", "answers": answers})
    assert r.status_code == 202, r.text
    await drained(app)
    return run_id


# ------------------------------------------------------------------ tickets and the crash guard


async def test_post_ticket_starts_a_run_in_the_background(app, client, rt, monkeypatch):
    seen = {}

    async def fake_start(rt_, run_id, text, address):
        seen.update(text=text, address=address)
        rt_.store.set_state(run_id, RunState.TRIAGED)

    monkeypatch.setattr(runner, "start", fake_start)
    r = await client.post("/tickets", json={"text": "new hire Priya", "from": "tgarcia"})
    assert r.status_code == 202
    await drained(app)

    run_id = r.json()["run_id"]
    assert seen == {"text": "new hire Priya", "address": TAYLOR}
    assert rt.store.get_run(run_id).state == RunState.TRIAGED
    assert (await client.get("/runs")).json()[0] | {"created_at": None, "updated_at": None} == {
        "id": run_id, "state": "TRIAGED", "from": TAYLOR, "subject": "new hire Priya",
        "created_at": None, "updated_at": None}


async def test_a_crashed_background_task_escalates_the_run(app, client, rt, monkeypatch):
    async def broken_start(*args):
        raise RuntimeError("model API exploded")

    monkeypatch.setattr(runner, "start", broken_start)
    run_id = (await client.post("/tickets", json={"text": "x", "from": TAYLOR})).json()["run_id"]
    await drained(app)

    assert rt.store.last_event(run_id, "error").payload == {"where": "start",
                                                            "error": "RuntimeError: model API exploded"}
    state = rt.store.last_event(run_id, "state").payload
    assert state["to"] == "ESCALATED" and state["kind"] == "error"


async def test_bad_requests_are_rejected(client):
    assert (await client.post("/tickets", json={"text": "", "from": TAYLOR})).status_code == 422
    assert (await client.get("/runs/999")).status_code == 404
    assert (await client.get("/runs/999/events")).status_code == 404
    assert (await client.post("/followups/999/run-now")).status_code == 404


# ------------------------------------------------------------------ snapshot


async def test_snapshot_of_a_run_awaiting_approval(client, rt, gated):
    run_id = gated()
    snap = (await client.get(f"/runs/{run_id}")).json()

    assert snap["run"]["state"] == "AWAITING_APPROVAL"
    assert snap["last_seq"] == rt.store.events(run_id)[-1].seq
    assert snap["ticket"] == {"id": snap["ticket"]["id"], "text": "new hire Priya", "from": TAYLOR}
    assert snap["findings"] == ["Marcus is in Accounting"]
    decisions = {s["id"]: s["decision"] for s in snap["steps"]}
    assert decisions["buy_seat"] == decisions["tap"] == "approve"
    assert decisions["create_user"] == "auto"
    assert all(s["status"] == "pending" and s["rule_id"] for s in snap["steps"])
    assert snap["approval"] is None and snap["outcome"] is None
    assert snap["oob"] == {"needed": False, "confirmed": None}  # onboarding has no out-of-band check


# ------------------------------------------------------------------ approval


async def test_approve_executes_with_the_answers(app, client, rt, gated, tenant):
    run_id = await approved_run(app, client, gated, upn_format="pnair")

    assert rt.store.get_run(run_id).state == RunState.DONE
    assert tenant.user_by_upn(f"pnair@{DOMAIN}")
    snap = (await client.get(f"/runs/{run_id}")).json()
    assert {s["status"] for s in snap["steps"]} == {"verified"}
    assert snap["steps"][0]["params"]["userPrincipalName"] == f"pnair@{DOMAIN}"  # the effective plan
    assert snap["approval"]["answers"] == {"upn_format": "pnair"}
    assert snap["links"][0]["url"].startswith(f"{ORIGIN}/l/")
    assert TAP_VALUE not in everything(rt, run_id) and TAP_VALUE not in json.dumps(snap)


async def test_a_second_approval_is_refused_before_and_after_execution(app, client, gated, tenant):
    run_id = gated()
    first = await client.post(f"/runs/{run_id}/approval", json={"by": "tgarcia"})
    second = await client.post(f"/runs/{run_id}/approval", json={"by": "tgarcia"})  # execution still in flight
    assert (first.status_code, second.status_code) == (202, 409)
    assert first.json() == {"state": "EXECUTING"}
    await drained(app)
    assert (await client.post(f"/runs/{run_id}/approval", json={"by": "tgarcia"})).status_code == 409


async def test_a_non_approver_is_refused(client, rt, gated, tenant):
    run_id = gated()
    r = await client.post(f"/runs/{run_id}/approval", json={"by": "jlee"})
    assert r.status_code == 409 and "approver list" in r.json()["detail"]
    assert rt.store.get_run(run_id).state == RunState.AWAITING_APPROVAL


async def test_reject_escalates_without_touching_the_tenant(app, client, rt, gated, tenant):
    run_id = gated()
    r = await client.post(f"/runs/{run_id}/approval", json={"by": "tgarcia", "decision": "reject"})
    assert r.json() == {"state": "ESCALATED"}
    await drained(app)
    assert rt.store.last_event(run_id, "state").payload["kind"] == "rejected"
    assert not tenant.calls


async def test_resume_needs_an_executing_run(client, gated):
    assert (await client.post(f"/runs/{gated()}/resume")).status_code == 409


# ------------------------------------------------------------------ run now


async def test_run_now_assigns_the_license(app, client, rt, gated, tenant):
    run_id = await approved_run(app, client, gated)
    followup = scheduler.for_step(rt.store, run_id, "assign_license_monday")

    assert (await client.post(f"/followups/{followup.id}/run-now")).status_code == 202
    await drained(app)

    priya = tenant.user_by_upn(f"priya.nair@{DOMAIN}")["id"]
    assert SPB in tenant.licenses[priya]
    assert scheduler.get(rt.store, followup.id).status == "done"
    assert (await client.post(f"/followups/{followup.id}/run-now")).status_code == 409


# ------------------------------------------------------------------ one-time link


async def test_link_shows_metadata_then_reveals_the_tap_once(app, client, rt, gated, tenant):
    run_id = await approved_run(app, client, gated)
    token = links.for_run(rt.store, run_id)[0].token

    view = (await client.get(f"/l/{token}")).json()
    assert view["userPrincipalName"] == f"priya.nair@{DOMAIN}" and not view["viewed"]
    assert TAP_VALUE not in json.dumps(view)

    revealed = await client.post(f"/l/{token}/reveal")
    assert revealed.status_code == 200 and revealed.json()["temporaryAccessPass"] == TAP_VALUE
    assert links.by_token(rt.store, token).ciphertext == ""
    assert (await client.post(f"/l/{token}/reveal")).status_code == 410

    viewed = rt.store.last_event(run_id, "link_viewed").payload
    assert viewed["step_id"] == "send_link"
    assert TAP_VALUE not in everything(rt, run_id)
    assert (await client.get(f"/runs/{run_id}")).json()["links"][0]["url"] is None


async def test_expired_and_unknown_links(app, client, rt, gated, tenant):
    run_id = await approved_run(app, client, gated)
    link = links.for_run(rt.store, run_id)[0]
    with rt.store.session() as s:
        row = s.get(Link, link.id)
        row.expires_at = utcnow() - timedelta(minutes=1)
        s.add(row)
        s.commit()

    assert (await client.get(f"/l/{link.token}")).status_code == 410
    assert (await client.post(f"/l/{link.token}/reveal")).status_code == 410
    assert (await client.get("/l/nope")).status_code == 404
    assert (await client.post("/l/nope/reveal")).status_code == 404


# ------------------------------------------------------------------ SSE: the generator


async def collect(stream) -> list:
    return [item async for item in stream]


async def test_stream_replays_in_order_and_resumes_after_a_seq(rt, gated):
    run_id = gated()
    rt.store.set_state(run_id, RunState.ESCALATED)  # terminal, so the stream ends after the replay
    seqs = [e.seq for e in rt.store.events(run_id)]

    items = await collect(api.event_stream(rt.store, run_id))
    assert [int(i.id) for i in items[:-1]] == seqs and items[-1].event == "end"

    items = await collect(api.event_stream(rt.store, run_id, after_seq=3))
    assert [int(i.id) for i in items[:-1]] == seqs[3:]


async def test_stream_delivers_live_events_once_in_order_then_ends(rt, gated):
    run_id = gated()
    before = rt.store.events(run_id)[-1].seq
    task = asyncio.create_task(collect(api.event_stream(rt.store, run_id, poll=0.01)))
    await asyncio.sleep(0.02)  # replay done, waiting on the live queue

    for i in range(3):
        rt.store.append_event(run_id, "finding", {"text": f"live {i}", "t": 2.0})
        await asyncio.sleep(0)
    rt.store.set_state(run_id, RunState.ESCALATED, kind="test")
    items = await asyncio.wait_for(task, 1)

    seqs = [int(i.id) for i in items[:-1]]
    assert seqs == list(range(1, before + 5))
    assert [i.data["text"] for i in items if i.event == "finding"][-3:] == ["live 0", "live 1", "live 2"]
    assert items[-1].event == "end" and items[-1].data["state"] == "ESCALATED"


async def test_stream_stays_open_on_a_finished_run_while_work_is_running(rt, gated):
    run_id = gated()
    rt.store.set_state(run_id, RunState.ESCALATED)
    busy = [True]
    task = asyncio.create_task(collect(api.event_stream(rt.store, run_id, busy=lambda: busy[0], poll=0.01)))
    await asyncio.sleep(0.02)
    assert not task.done()

    rt.store.append_event(run_id, "step_verified", {"step_id": "x-run", "action": "license.assign"})
    busy[0] = False
    items = await asyncio.wait_for(task, 1)
    assert [i.event for i in items[-2:]] == ["step_verified", "end"]


# ------------------------------------------------------------------ SSE: over HTTP


async def test_sse_over_http_with_last_event_id(app, client, rt, gated, tenant):
    run_id = await approved_run(app, client, gated)
    n = rt.store.events(run_id)[-1].seq

    r = await client.get(f"/runs/{run_id}/events")
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    assert [int(e["id"]) for e in events[:-1]] == list(range(1, n + 1))
    assert events[-1]["event"] == "end" and events[-1]["data"] == {"state": "DONE", "last_seq": n}
    assert TAP_VALUE not in r.text

    events = parse_sse((await client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": "10"})).text)
    assert [int(e["id"]) for e in events[:-1]] == list(range(11, n + 1))
    events = parse_sse((await client.get(f"/runs/{run_id}/events?after={n}")).text)
    assert [e["event"] for e in events] == ["end"]


# ------------------------------------------------------------------ close-out


async def test_snapshot_shows_the_close_out_with_the_link_filled_in(app, client, rt, gated, tenant):
    run_id = await approved_run(app, client, gated)

    closeout = (await client.get(f"/runs/{run_id}")).json()["closeout"]
    assert closeout["checks"] and all(c["ok"] for c in closeout["checks"])
    assert closeout["time_entry"]["work_type"] == "AI Automation"
    reply = next(m for m in closeout["messages"] if m["kind"] == "reply")
    token = links.for_run(rt.store, run_id)[0].token
    assert f"{ORIGIN}/l/{token}" in reply["text"] and "{{link}}" not in reply["text"]
    assert token not in everything(rt, run_id)  # filled in when served, never stored

    await client.post(f"/l/{token}/reveal")
    closeout = (await client.get(f"/runs/{run_id}")).json()["closeout"]
    reply = next(m for m in closeout["messages"] if m["kind"] == "reply")
    assert token not in reply["text"] and "already opened" in reply["text"]


async def test_snapshot_closeout_is_empty_before_execution(client, gated):
    run_id = gated()
    assert (await client.get(f"/runs/{run_id}")).json()["closeout"] == {
        "checks": [], "messages": [], "blocked": [], "time_entry": None}


# ------------------------------------------------------------------ "Save as playbook" (Build-W4 Day 18)


async def test_a_finished_no_playbook_run_can_be_saved_as_a_playbook(app, client, rt, restore_gated):
    rt.closeout_model = drafts_model(reply="Hi Sophie, it's restored; nothing is lost.")
    run_id, _, _ = restore_gated()
    assert (await client.get(f"/runs/{run_id}")).json()["can_save_playbook"] is False  # not finished yet

    r = await client.post(f"/runs/{run_id}/approval", json={"by": "tgarcia"})
    assert r.status_code == 202, r.text
    await drained(app)
    snap = (await client.get(f"/runs/{run_id}")).json()
    assert snap["run"]["state"] == "DONE" and snap["can_save_playbook"] is True


async def test_a_playbook_run_or_an_escalated_one_cant_be_saved(app, client, rt, gated, restore_gated, tenant):
    done = await approved_run(app, client, gated)
    assert rt.store.get_run(done).state == RunState.DONE
    assert (await client.get(f"/runs/{done}")).json()["can_save_playbook"] is False      # it has a playbook

    rejected, _, _ = restore_gated()
    await client.post(f"/runs/{rejected}/approval", json={"by": "tgarcia", "decision": "reject"})
    await drained(app)
    assert (await client.get(f"/runs/{rejected}")).json()["can_save_playbook"] is False  # escalated, nothing done


# ------------------------------------------------------------------ playbooks: save, write, approve, autonomy
# (Build-Day19 part 3)

RESTORE = "file.restore_version"


async def done_restore(rt, restore_gated) -> int:
    run_id, _, _ = restore_gated()
    assert await runner.approve(rt, run_id, by=TAYLOR) == RunState.DONE
    return run_id


async def test_a_draft_needs_a_finished_no_playbook_run(client, rt, gated, restore_gated):
    rt.drafter_model = drafter_model(LEARNED_RESTORE.read_text())
    waiting, _, _ = restore_gated()
    r = await client.post(f"/runs/{waiting}/playbook-draft", json={})
    assert r.status_code == 409 and "can't be saved as a playbook" in r.json()["detail"]

    run_id = gated()  # onboarding: it has a playbook
    await runner.approve(rt, run_id, by=TAYLOR)
    assert (await client.post(f"/runs/{run_id}/playbook-draft", json={})).status_code == 409


async def test_save_as_playbook_then_approve_at_a3(client, rt, restore_gated):
    rt.drafter_model = drafter_model(LEARNED_RESTORE.read_text())
    run_id = await done_restore(rt, restore_gated)
    assert (await client.get(f"/runs/{run_id}")).json()["can_save_playbook"] is True

    draft = (await client.post(f"/runs/{run_id}/playbook-draft", json={"notes": ["check the times"]})).json()
    assert (draft["intent"], draft["ok"], draft["problems"], draft["source"], draft["source_run_id"]) == (
        RESTORE, True, [], "agent_run", run_id)

    r = await client.post(f"/playbooks/{RESTORE}/approve", json={
        "yaml": draft["yaml"], "autonomy": "A3", "by": "aditya", "source": "agent_run", "source_run_id": run_id})
    assert r.status_code == 201, r.text
    assert {k: r.json()[k] for k in ("version", "source", "autonomy", "ceiling", "approved_by")} == {
        "version": 1, "source": "agent_run", "autonomy": "A3", "ceiling": "A3", "approved_by": "aditya"}

    one = (await client.get(f"/playbooks/{RESTORE}")).json()
    assert one["yaml"] == draft["yaml"] and [v["status"] for v in one["versions"]] == ["approved"]
    assert {p["intent"] for p in (await client.get("/playbooks")).json()} == {
        "mfa.reset", "user.offboard", "user.onboard", RESTORE}
    assert (await client.get(f"/runs/{run_id}")).json()["can_save_playbook"] is True  # still a source run


async def test_the_draft_can_be_renamed(client, rt, restore_gated):
    text = LEARNED_RESTORE.read_text().replace(f"intent: {RESTORE}", "intent: sharepoint.restore_version")
    rt.drafter_model = drafter_model(text)
    run_id = await done_restore(rt, restore_gated)

    draft = (await client.post(f"/runs/{run_id}/playbook-draft", json={"intent": "sharepoint.restore_version"})).json()
    assert draft["intent"] == "sharepoint.restore_version" and draft["ok"]


async def test_a_rejected_approval_is_a_422_with_every_problem(client, rt, restore_gated):
    run_id = await done_restore(rt, restore_gated)
    widened = LEARNED_RESTORE.read_text().replace("[file.restoreVersion]", "[file.restoreVersion, tap.create]")

    r = await client.post(f"/playbooks/{RESTORE}/approve", json={
        "yaml": widened, "autonomy": "A2", "by": "aditya", "source": "agent_run", "source_run_id": run_id})
    assert r.status_code == 422
    assert "allowed_actions has tap.create, which the run it was learned from never did." in r.json()["detail"]
    assert (await client.get(f"/playbooks/{RESTORE}")).status_code == 404


async def test_a_written_playbook_is_drafted_then_capped_below_a3(client, rt):
    rt.drafter_model = drafter_model(LEARNED_RESTORE.read_text())
    draft = (await client.post("/playbooks", json={
        "intent": RESTORE, "text": "Restore the version of a file the requester means.",
        "allowed_actions": ["file.restoreVersion"], "by": "aditya"})).json()
    assert draft["ok"] and draft["source"] == "written" and draft["source_run_id"] is None

    body = {"yaml": draft["yaml"], "by": "aditya", "source": "written"}
    assert (await client.post(f"/playbooks/{RESTORE}/approve", json=body | {"autonomy": "A3"})).status_code == 422
    r = await client.post(f"/playbooks/{RESTORE}/approve", json=body | {"autonomy": "A2"})
    assert r.status_code == 201 and r.json()["ceiling"] == "A2"

    r = await client.post(f"/playbooks/{RESTORE}/autonomy", json={"level": "A3", "by": "aditya"})
    assert r.status_code == 422 and "A3 needs a successful approved run" in r.json()["detail"][0]
    r = await client.post(f"/playbooks/{RESTORE}/autonomy", json={"level": "A1", "by": "aditya"})
    assert r.status_code == 200 and r.json()["autonomy"] == "A1"


async def test_autonomy_on_an_unknown_intent_is_a_404(client):
    assert (await client.post("/playbooks/file.nothing/autonomy", json={"level": "A2", "by": "x"})).status_code == 404
    assert (await client.get("/playbooks/file.nothing")).status_code == 404
