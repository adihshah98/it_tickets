# Week 3 Day 13 (part 2): `authMethod.delete` + `riskyUsers.get` + OOB/Dennis (bullets 2 & 3)

## Context

The `mfa.reset` playbook landed in part 1 (`backend/tech/playbooks/mfa.reset.yaml`) referencing an action
and a read tool that do not exist yet. The suite is **already red in exactly three places** — the
intentional tripwires:

- `test_catalogue.py::test_every_allowed_action_is_in_the_catalogue[mfa.reset]` — `authMethod.delete` ∉ `ACTIONS`
- `test_catalogue.py::test_every_catalogue_action_is_used_by_some_playbook` — same
- `test_playbooks.py::test_must_investigate_names_real_read_tools[mfa.reset]` — `riskyUsers.get` ∉ `READS`

The linter already anticipates the work: `safety/lint.py:56-57` maps `oob_confirmed → {tap.create,
authMethod.delete}` and `old_methods_removed → {authMethod.delete}`.

This plan implements **Day 13 bullets 2 and 3 together** (user's choice):

- **Bullet 2** — the `authMethod.delete` write action (the four-touch pattern) + the `riskyUsers.get`
  read tool, with FakeTenant routes to back them.
- **Bullet 3** — the `requester.confirmOutOfBand` mock + `oob_confirmed` run-level event, the gate/
  `accept()` OOB precondition for any `security.credential` step on an `mfa.reset` run, the Dennis
  read-layer happy-path mock, and the MFA DoD probes (`oob_confirmed`, `old_methods_removed`) plus the
  mfa-subject resolution that makes `tap_active`/`message_sent` work when there is no `user.create` step.

**Not in scope** (later items): the Dennis/suspicious/`reports@` eval tests and the full live run
(Day 15 eval harness); any `web/` work; the replanner/escalation write-up (Day 14). `security.credential`
is **already** in `APPROVE_CLASSES` (`gate.py:32`) — no gate risk-class change.

The outcome: the three tripwires go green, and an `mfa.reset` run can delete a stale auth method, be
gated to require OOB first, and verify its DoD — with the four demo refusal paths left genuine.

---

## Part A — `authMethod.delete` action (four-touch)

Mirror `tap.create` (the only other `security.credential` action) and `group.removeMember` (the DELETE idiom).

### A1. Params + Step (`core/models/`)

`params.py` — new model after `GroupAddOwnerParams`. Use the **methodType enum + methodId** shape
(user's choice): a small literal the handler maps to the Graph collection segment.

```python
AuthMethodType = Literal["authenticator", "phone", "fido2", "softwareOath", "windowsHelloForBusiness"]

class AuthMethodDeleteParams(Params):
    userId: ObjectIdOrRef = alias("userId", "user_id")
    methodType: AuthMethodType = alias("methodType", "method_type",
        description="Which kind of method to delete; the handler maps it to the Graph endpoint")
    methodId: ObjectId = alias("methodId", "method_id",
        description="The method's id, observed from authMethods.list")
```
- `methodId` is `ObjectId` (GUID-only, so the linter's grounded rule requires it to have been read).
- The method-type → Graph-path-segment map lives in the handler, not the params.

`plan.py` — add `AuthMethodDeleteStep(StepBase)` with `action: Literal["authMethod.delete"]` and
`params: AuthMethodDeleteParams`; add it to **both** the `Step` union (lines 104-107) **and** the
`STEP_TYPES` tuple (lines 112-114). Import the params model at the top.

`core/models/__init__.py` — add `AuthMethodDeleteParams` and `AuthMethodDeleteStep` to the import blocks
and `__all__` (so every `from tech.core.models import …` site keeps working).

### A2. Handler (`graph/writes.py`)

New `AuthMethodDelete(Handler)` in the credentials section (after `TapCreate`), registered in `HANDLERS`
as `"authMethod.delete"`. Pattern: idempotent `existing`, verify-by-absence read against the same
endpoint `authMethods.list` uses.

```python
AUTH_METHOD_SEGMENTS = {
    "authenticator": "microsoftAuthenticatorMethods",
    "phone": "phoneMethods",
    "fido2": "fido2Methods",
    "softwareOath": "softwareOathMethods",
    "windowsHelloForBusiness": "windowsHelloForBusinessMethods",
}

class AuthMethodDelete(Handler):
    def _path(self, p):
        return f"/users/{p['userId']}/authentication/{AUTH_METHOD_SEGMENTS[p['methodType']]}/{p['methodId']}"

    async def _method_ids(self, ctx, p) -> set[str]:
        return {m["id"].lower() async for m in
                ctx.graph.list(f"/users/{p['userId']}/authentication/methods", retry_replication=True)}

    async def existing(self, ctx, p):       # already gone -> idempotent success
        if p["methodId"].lower() not in await self._method_ids(ctx, p):
            return {"userId": p["userId"], "methodId": p["methodId"]}
        return None

    async def snapshot(self, ctx, p):
        return {"methodPresent": True, "methodType": p["methodType"]}

    async def act(self, ctx, p):
        try:
            await ctx.graph.delete(self._path(p), retry_replication=True)
        except GraphError as exc:            # swallow a 404 like GroupRemoveMember
            if not (exc.status == 404 or "does not exist" in exc.message.lower()):
                raise
        return {"userId": p["userId"], "methodId": p["methodId"]}

    async def verify(self, ctx, p, output):
        gone = output["methodId"].lower() not in await self._method_ids(ctx, p)
        return gone, {"methodPresent": not gone, "methodId": output["methodId"]}
```
Note: `authMethods.list` reads the **aggregated** `/authentication/methods` collection, whose entries
carry the per-method GUID — so verify-by-absence works regardless of method type (the type-specific
segment is only needed for the DELETE itself).

### A3. Catalogue (`execution/catalogue.py`)

Import `AuthMethodDeleteParams`; add an `ActionSpec` to the `ACTIONS` list (after `group.addOwner`):

```python
ActionSpec(
    "authMethod.delete", AuthMethodDeleteParams, "security.credential",
    "DELETE /users/{userId}/authentication/{methodType}/{methodId}",
    "GET /users/{userId}/authentication/methods: the method is gone",
    "the user re-registers a method (a deleted MFA method can't be restored by us)",
    handler=HANDLERS["authMethod.delete"],
),
```
`compensate` must be a non-empty string (`test_every_action_has_a_verify_read_and_compensation`).
`test_catalogue_and_plan_schema_cover_the_same_actions` requires `ACTIONS` and `STEP_TYPES` to carry the
**same `AuthMethodDeleteParams` object** — the single import satisfies this.

---

## Part B — `riskyUsers.get` read tool

### B1. Read fn + registry (`graph/reads.py`)

Add an async read after `auth_methods`, returning a trimmed, best-effort dict (P1 gives limited detail;
`riskyUsers` 404s for a never-risky user). Register as `"riskyUsers.get"` in `READS` (the loader strips
`riskyUsers.get(subject)` → `riskyUsers.get`, so the key must match exactly).

```python
async def risky_user(graph: GraphClient, user_id: str) -> dict:
    """A user's Identity Protection risk state (best-effort; P1 gives limited detail, P2 full).
    Returns {"risky": False} when the user is not flagged (Graph 404s)."""
    try:
        r = await graph.get(f"/identityProtection/riskyUsers/{user_id}")
    except GraphError as exc:
        if exc.status in (403, 404):     # 404 = not risky; 403 = tenant lacks the licence/role
            return {"risky": False, "detail": "not flagged" if exc.status == 404 else "not available"}
        raise
    return {"risky": True, "riskLevel": r.get("riskLevel"), "riskState": r.get("riskState"),
            "riskLastUpdatedDateTime": r.get("riskLastUpdatedDateTime")}
```

### B2. Investigator wrapper (`agents/investigator.py`)

Add a thin `risky_user(ctx, user_id)` wrapper calling `_read(ctx, "riskyUsers.get", user_id=user_id)`
next to `sign_ins`/`auth_methods`, and add it to the `READ_TOOLS` list so the investigator can call it.

---

## Part C — OOB confirmation (bullet 3: mock + gate precondition)

`requester.confirmOutOfBand` is **not a plan step**; it is a run-level event a UI button records.

### C1. Event type + mock (`core/store.py`, `execution/mocks.py`)

- `core/store.py` — add `"oob_confirmed"` to `EVENT_TYPES` (frozenset, line 41-45).
- `execution/mocks.py` — a small helper (not a `Handler`) the API/CLI calls for the "Manager confirmed
  via SMS" button; records the event:
  ```python
  def confirm_out_of_band(store: Store, run_id: int, *, by: str, via: str = "SMS") -> None:
      store.append_event(run_id, "oob_confirmed", {"by": by, "via": via})
  ```
  (Keep the signature small; the API wiring/button is UI scope but the function lives here now so the
  gate/DoD can rely on the event, and tests can call it directly.)

### C2. Gate precondition (`safety/gate.py`)

The requirement: **no `security.credential` step may run on an `mfa.reset` run before `oob_confirmed`.**
`gate.evaluate` is pure (no store), so thread the fact in as a boolean, mirroring `requester_approved`:

- Add `oob_confirmed: bool = False` param to `evaluate` (and `_step`), and have `check` read it from the
  store (`store.last_event(run_id, "oob_confirmed") is not None`) before calling `evaluate`.
- In `_risk_rule`/`_step`: when `"mfa.reset" in intents` and `risk == "security.credential"` and not
  `oob_confirmed`, add a **block** verdict (rule id `mfa.oob_required`, reason "a credential reset needs
  out-of-band confirmation first"). Place it so it participates in `final()` like the other verdicts.
- `check` callers (`runner.start`) pass `oob_confirmed=…`. At plan-check time OOB is normally not yet
  recorded, so the credential step gates to **block** → the run escalates/holds until OOB; the real
  enforcement point for ordering is `accept()` (next).

### C3. `accept()` refuses EXECUTING until OOB (`runner.py`)

In `accept()` (after the gate-block check, before `transition(..., S.EXECUTING)`): if the run's intents
include `mfa.reset` and the plan has any `security.credential` step (look up each step's class via
`ACTIONS[step.action].risk(...)`), and there is no `oob_confirmed` event, raise
`RunError("out-of-band confirmation is required before this MFA reset can run")`. This guarantees OOB
cannot be reordered after a credential write.

---

## Part D — Dennis read-layer happy-path mock (bullet 3)

Per the Decisions note: scoped to **Dennis's single GUID behind a setting**, in the **read layer**
(`run_read`), returning a clean 14-day `signIns.list` and a known Authenticator method, recorded as a
normal `tool_call` so the Transcript and eval harness see it like a live read. The suspicious (#12) and
`reports@` (#20) paths must read real/seeded data.

- Dennis's GUID is `4de26d55-a0ad-487f-837c-3d6bec07fce6` (from `tests/fixtures/spike_plans/mfa.json`).
- `graph/reads.py` — a module constant `DENNIS_MOCK` gated by env (e.g. `MFA_DEMO_MOCK` /
  `settings.mfa_demo_mock`), plus a `_dennis_override(tool, args)` that returns clean data **only** when
  the flag is on, the tool is `signIns.list`/`authMethods.list`, and `user_id == DENNIS_GUID`:
  - `signIns.list` → a short list of clean US interactive sign-ins over the last 14 days (errorCode 0,
    consistent city).
  - `authMethods.list` → one Authenticator method with a fixed GUID (so the plan can delete it).
- Hook it at the **top of `run_read`**: if an override applies, record it via `transcript.record(tool,
  args, result)` and return it (bypassing the real Graph call) so it is indistinguishable from a live
  read. When the flag is off or the GUID differs, fall through to the real read.
- Add a `mfa_demo_mock: bool = False` field to `Settings` (read from env `MFA_DEMO_MOCK`) and thread it
  to `run_read`’s override check (module-level read of the env is acceptable and matches "behind a
  setting"; prefer the Settings field if easily reachable from `run_read`’s callers).

Keep the mock narrow: GUID + two tools only, so #12/#20 stay genuine.

---

## Part E — MFA DoD probes (`execution/dod.py`)

The playbook DoD is: `oob_confirmed`, `old_methods_removed`, `tap_active`, `message_sent`,
`followup_scheduled`. `followup_scheduled` already handles `verify_new_method` (dod.py:270). The others:

### E1. An mfa-subject accessor on `DodContext`

MFA runs have no `user.create` step, so `tap_active`/`message_sent` (which key on `new_user()`) must
resolve the subject from the plan — mirror `leaver()` (dod.py:73-82):

```python
def subject(self) -> str | None:
    """The MFA subject's id: taken from the first credential/tap/link step's params."""
    order = ["authMethod.delete", "tap.create", "followup.schedule"]
    for action in order:
        for step in self.plan.steps:
            if step.action == action:
                return step.params.userId
    return None
```
Generalise `tap_active` to use `self.subject()` when there is no `new_user()` (keep onboarding behaviour
unchanged: onboarding has a `user.create`, offboarding/MFA don't). `message_sent` already keys on the
link recipient / requester, not `new_user()` — confirm it needs no change; if it references the subject,
route it through `subject()` too.

### E2. `old_methods_removed` probe

Derive the targets from the run (Day-8 pattern), not the `$plan.methods_to_remove` string. Add an
accessor + probe:
```python
# DodContext
def methods_to_remove(self) -> dict[str, str]:    # methodId(lower) -> userId
    return {s.params.methodId.lower(): s.params.userId
            for s in self.plan.steps if s.action == "authMethod.delete"}

@probe("old_methods_removed")
async def old_methods_removed(ctx, args):
    want = ctx.methods_to_remove()
    if not want:
        return Probe("old_methods_removed", True, "the plan removes no methods")
    # group by user; read /authentication/methods once per user; each wanted id must be absent
    ... returns False listing any still-present method ids, else "removed N".
```

### E3. `oob_confirmed` probe

```python
@probe("oob_confirmed")
async def oob_confirmed(ctx, args):
    ev = ctx.store.last_event(ctx.run_id, "oob_confirmed")
    return Probe("oob_confirmed", ev is not None,
                 f"confirmed by {ev.payload['by']}" if ev else "no out-of-band confirmation was recorded")
```

---

## Part F — FakeTenant routes (`tests/fake_tenant.py`)

The executor's verify and the read tool need these, none of which exist yet:

1. **State**: `self.auth_methods: dict[str, list[dict]] = {}` in `__init__` (uid → list of
   `{"id", "@odata.type", "displayName"}`), plus a `self.risky: dict[str, dict] = {}`. A setup helper
   `add_auth_method(uid, method_type, mid=None, name=None)`.
2. **GET** `/users/{id}/authentication/methods` — return `{"value": self.auth_methods.get(uid, [])}`
   (new `case "GET", "/authentication/methods"` in the user-scoped match block).
3. **DELETE** `/users/{id}/authentication/{segment}/{mid}` — remove the method by id from the user's
   list; 404 when absent (mirror the DELETE-member idiom at lines 89-95). Match any of the
   `AUTH_METHOD_SEGMENTS` values with a regex on `rest`.
4. **GET** `/identityProtection/riskyUsers/{id}` — tenant-scoped, so add it in `_route` **before** the
   `/users/...` regex (like `/subscribedSkus`): return the seeded risk dict, else 404 (not flagged).

The existing TAP POST/GET routes stay; `tap_active`/`old_methods_removed` read the same auth-method
state. Note the DELETE must also update `self.taps` nothing — only the `auth_methods` list.

---

## Critical files

- `backend/tech/core/models/params.py`, `plan.py`, `__init__.py` — params + step union + exports
- `backend/tech/execution/catalogue.py` — `authMethod.delete` ActionSpec
- `backend/tech/graph/writes.py` — `AuthMethodDelete` handler + `HANDLERS` entry
- `backend/tech/graph/reads.py` — `risky_user` read + `READS` entry + Dennis `run_read` override
- `backend/tech/agents/investigator.py` — `risky_user` wrapper + `READ_TOOLS`
- `backend/tech/core/store.py` — `oob_confirmed` event type
- `backend/tech/execution/mocks.py` — `confirm_out_of_band` helper
- `backend/tech/safety/gate.py` — OOB precondition (`oob_confirmed` param + `mfa.oob_required` rule)
- `backend/tech/runner.py` — `accept()` OOB refusal + thread `oob_confirmed` into `gate.check`
- `backend/tech/execution/dod.py` — `subject()` accessor, `old_methods_removed`, `oob_confirmed` probes;
  generalise `tap_active`
- `backend/tech/core/config.py` — `mfa_demo_mock` setting
- `backend/tests/fake_tenant.py` — auth-methods + riskyUsers routes and state

## Verification

Offline, no network (`uv run` from `backend/`):

1. **The three tripwires go green**:
   `uv run pytest tests/test_catalogue.py tests/test_playbooks.py -q` → all pass (including
   `test_catalogue_and_plan_schema_cover_the_same_actions` and
   `test_every_action_has_a_verify_read_and_compensation`).
2. **Full suite + lint**: `uv run pytest -q` and `uv run ruff check backend` → green. The models split
   proof still holds (no importer breakage from the new `__init__` exports).
3. **New read tool**: a `test_reads.py` case for `riskyUsers.get` against FakeTenant — flagged user →
   `{"risky": True, ...}`; unflagged (404) → `{"risky": False}` — mirroring the `users.memberOf` test.
4. **New handler**: an executor/`FakeTenant` test — seed a user with an Authenticator method, run an
   `authMethod.delete` step, assert the method is gone and `verify` returns ok; a second run (method
   already absent) is idempotent via `existing`.
5. **DoD probes** (`test_dod.py`, subset style with `only=(...)`): `old_methods_removed` passes after a
   delete and fails if the method is still present; `oob_confirmed` passes only after
   `mocks.confirm_out_of_band(...)`; `tap_active` passes for an MFA run (subject from the plan, no
   `user.create`).
6. **OOB precondition**: a unit test — an `mfa.reset` plan with a `security.credential` step and no
   `oob_confirmed` → `gate.evaluate` blocks that step (`mfa.oob_required`); `accept()` raises `RunError`
   until `confirm_out_of_band` is recorded, then transitions to EXECUTING. Assert a non-mfa onboarding
   plan's gate output is **unchanged** (OOB rule is a no-op off `mfa.reset`).
7. **Dennis mock**: with the flag on, `run_read(..., "authMethods.list", user_id=DENNIS_GUID)` returns
   the seeded clean method and records a `tool_call`; with the flag off, or a different GUID, it falls
   through to the real/fake Graph. (The full suspicious/reports@ eval assertions are Day 15 scope.)
