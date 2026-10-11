"use client";

// The one-time link: who it's for first, then the TAP on an explicit click. Link previewers (Safe Links,
// Slack unfurls) only GET, so they can't burn it. The TAP lives in this component's state and nowhere else.

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, revealLink, viewLink } from "@/lib/api";
import type { LinkView } from "@/lib/types";
import { when } from "./ui";

const GONE = "This link has already been opened or has expired. Ask your IT contact for a new one.";

function message(e: unknown) {
  if (e instanceof ApiError && e.status === 404) return "This link doesn't exist.";
  if (e instanceof ApiError && e.status === 410) return GONE;
  return e instanceof ApiError ? e.detail : String(e);
}

export default function LinkPage() {
  const { token } = useParams<{ token: string }>();
  const [view, setView] = useState<LinkView | null>(null);
  const [tap, setTap] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    viewLink(token).then(setView, (e) => setError(message(e)));
  }, [token]);

  async function reveal() {
    setBusy(true);
    try {
      const result = await revealLink(token);
      setView(result);
      setTap(result.temporaryAccessPass);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <p className="rounded border border-neutral-200 bg-white p-6 text-sm">{error}</p>;
  if (!view) return <p className="text-sm text-neutral-500">Loading…</p>;
  const upn = view.userPrincipalName ?? "the new account";

  return (
    <div className="space-y-4 rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
      <h1 className="text-lg font-semibold">Sign-in details for {upn}</h1>
      <p className="text-sm text-neutral-600">
        Sent to {view.to}. This page shows the temporary access pass <strong>once</strong>; it expires {when(view.expiresAt)}.
      </p>
      {view.note && <p className="rounded bg-neutral-50 p-3 text-sm">{view.note}</p>}

      {tap ? (
        <>
          <div className="rounded border-2 border-emerald-500 p-4 text-center">
            <p className="text-xs uppercase tracking-wide text-neutral-500">Temporary access pass</p>
            <p className="mt-1 select-all font-mono text-2xl font-semibold">{tap}</p>
            <p className="mt-1 text-xs text-neutral-500">Copy it now: it won&apos;t be shown again.</p>
          </div>
          <ol className="ml-5 list-decimal space-y-1 text-sm">
            <li>Go to <span className="font-mono">https://aka.ms/mysecurityinfo</span> (or office.com) and sign in as <span className="font-mono">{upn}</span>.</li>
            <li>When asked, enter the temporary access pass above instead of a password.</li>
            <li>Set up the Microsoft Authenticator app, then you&apos;re in.</li>
            {view.includes.includes("outlook_mobile_app_tip") && (
              <li>On a phone, use the Outlook app for email; the built-in iOS Mail app can ask for admin approval.</li>
            )}
          </ol>
        </>
      ) : view.viewed ? (
        <p className="text-sm">{GONE}</p>
      ) : (
        <button
          type="button" disabled={busy} onClick={reveal}
          className="rounded bg-neutral-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
        >
          Show the temporary access pass
        </button>
      )}
    </div>
  );
}
