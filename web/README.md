# web

The workspace UI for the AI technician demo (Next.js, App Router). It only calls the backend API; see
[docs/demo/Build-Day7.md](../docs/demo/Build-Day7.md).

```bash
npm install
npm run dev          # http://localhost:3000; the API is expected at NEXT_PUBLIC_API_URL (default http://localhost:8000)
npm test             # vitest: the event-stream reducer, on a captured onboarding run
npm run typecheck && npm run lint && npm run build
```

- `/` inbox: paste a ticket (or pick a sample) and a sender; recent runs.
- `/runs/[id]` workspace: snapshot plus the replayed and live event stream (`lib/useRun.ts`, `lib/run.ts`).
- `/l/[token]` one-time link: metadata first, the TAP once on an explicit click.
