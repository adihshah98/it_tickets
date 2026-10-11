import { Suspense } from "react";
import Workspace from "@/components/Workspace";

// The run id is only known at request time, so the client component that reads it sits in Suspense.
export default function Page() {
  return (
    <Suspense fallback={<p className="text-sm text-neutral-500">Loading…</p>}>
      <Workspace />
    </Suspense>
  );
}
