import { Suspense } from "react";
import PlaybookPage from "@/components/PlaybookPage";

// The intent and ?run= are only known at request time, so the client component that reads them sits in Suspense.
export default function Page() {
  return (
    <Suspense fallback={<p className="text-sm text-neutral-500">Loading…</p>}>
      <PlaybookPage />
    </Suspense>
  );
}
