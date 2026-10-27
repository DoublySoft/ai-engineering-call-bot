import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Voice AI Lab",
  description: "Experimental lab for voice AI architectures",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="border-b border-border">
          <nav className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3 text-sm">
            <Link href="/" className="font-semibold">
              Voice AI Lab
            </Link>
            <Link href="/" className="text-muted hover:text-foreground">
              Dashboard
            </Link>
            <Link href="/calls" className="text-muted hover:text-foreground">
              Calls
            </Link>
            <Link href="/analytics" className="text-muted hover:text-foreground">
              Analytics
            </Link>
            <Link href="/telephony" className="text-muted hover:text-foreground">
              Telephony
            </Link>
            <Link href="/agents" className="text-muted hover:text-foreground">
              Agents
            </Link>
            <Link href="/calls/new" className="text-muted hover:text-foreground">
              New call
            </Link>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
