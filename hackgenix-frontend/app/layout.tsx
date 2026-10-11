import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono, Fraunces } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import Sidebar from "@/components/ui/Sidebar";
import MobileNav from "@/components/ui/MobileNav";
import { ThemeProvider, THEME_INIT_SCRIPT } from "@/components/ui/ThemeProvider";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
});
// Editorial display serif for large hero titles / headline numbers — used
// deliberately sparingly (see font-serif usage) so the product still reads
// as a serious intelligence platform, not a portfolio site.
const fraunces = Fraunces({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
  variable: "--font-fraunces",
});

export const metadata: Metadata = {
  title: "SignalForge — Feedback Intelligence",
  description: "From customer noise to decision-grade evidence.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${plexMono.variable} ${fraunces.variable}`} suppressHydrationWarning>
      <head>
        {/* Blocking — applies the persisted theme class to <html> before
            first paint so there is never a light-then-dark flash on reload. */}
        <Script id="theme-init" strategy="beforeInteractive">
          {THEME_INIT_SCRIPT}
        </Script>
      </head>
      <body className="flex min-h-screen flex-col bg-background text-foreground md:flex-row">
        <ThemeProvider>
          {/* Fixed left rail on desktop; a slim top bar takes its place on mobile. */}
          <Sidebar />
          <MobileNav />

          <div className="min-w-0 flex-1 overflow-y-auto">
            {/* Shared content header — desktop-only; MobileNav carries the
                same ThemeToggle on narrow viewports instead, so the control
                is never duplicated in two different implementations. */}
            <div className="hidden justify-end border-b border-border px-6 py-3 md:flex md:px-12">
              <ThemeToggle />
            </div>
            {children}
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
