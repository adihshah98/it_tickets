"use client";

// Drives the workspace: the snapshot, plus the event stream replayed from the start and then live.
// The snapshot is re-fetched (debounced) whenever an event makes it stale. The stream closes at `end`;
// `reopen()` picks it up again after the last event seen (a "run now" on a finished run).

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { ApiError, eventsUrl, getRun } from "./api";
import { initialView, reduce, REFRESH_ON } from "./run";
import type { RunEvent, Snapshot } from "./types";

const EVENT_TYPES = [
  "state", "triage", "tool_call", "finding", "plan", "lint_result", "gate_decision", "approval",
  "step_started", "step_retry", "step_verified", "step_failed", "replan", "done_check", "message_sent", "error",
  "playbook_drafted", "playbook_approved", "playbook_demoted", "audit_observed", "link_viewed", "message_blocked",
  "time_entry", "oob_confirmed", "end",
];

export function useRun(id: number) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [view, dispatch] = useReducer(reduce, initialView);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [generation, setGeneration] = useState(0);
  const last = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSnap(await getRun(id));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? (e.status ? `${e.status}: ${e.detail}` : e.detail) : String(e));
    }
  }, [id]);

  const scheduleRefresh = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(refresh, 200);
  }, [refresh]);

  useEffect(() => {
    // fetch-on-mount; the setState happens after the await
    // eslint-disable-next-line react-hooks/set-state-in-effect
    refresh();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [refresh]);

  useEffect(() => {
    const es = new EventSource(eventsUrl(id, last.current));
    es.onopen = () => setLive(true);
    for (const type of EVENT_TYPES) {
      es.addEventListener(type, (e) => {
        // "error" is also EventSource's own connection-error event, which carries no data
        if (!(e instanceof MessageEvent) || !e.data) {
          setLive(es.readyState === EventSource.OPEN);
          return;
        }
        const event: RunEvent = { seq: Number(e.lastEventId) || 0, type, data: JSON.parse(e.data) };
        if (type === "end") {
          es.close();
          setLive(false);
        } else {
          last.current = Math.max(last.current, event.seq);
        }
        dispatch(event);
        if (REFRESH_ON.has(type)) scheduleRefresh();
      });
    }
    return () => {
      es.close();
      setLive(false);
    };
  }, [id, generation, scheduleRefresh]);

  const reopen = useCallback(() => setGeneration((g) => g + 1), []);

  return { snap, view, error, live, refresh, reopen };
}
