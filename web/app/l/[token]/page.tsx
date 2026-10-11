import type { Metadata } from "next";
import { Suspense } from "react";
import LinkPage from "@/components/LinkPage";

// The token in the URL is the secret: keep it out of search indexes and Referer headers.
export const metadata: Metadata = {
  title: "Sign-in details",
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

export default function Page() {
  return (
    <Suspense fallback={<p className="text-sm text-neutral-500">Loading…</p>}>
      <LinkPage />
    </Suspense>
  );
}
