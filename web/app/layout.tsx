import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Technician",
  description: "Ticket inbox and workspace for the AI technician demo",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-neutral-50 text-neutral-900">
        <nav className="border-b border-neutral-200 bg-white">
          <div className="mx-auto flex max-w-4xl items-center gap-4 px-4 py-2">
            <Link href="/" className="text-sm font-semibold">AI Technician</Link>
            <span className="text-xs text-neutral-400">Northwind Parking</span>
          </div>
        </nav>
        <main className="mx-auto max-w-4xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
