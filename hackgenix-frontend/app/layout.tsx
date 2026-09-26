import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono, Fraunces } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/ui/Sidebar";
import MobileNav from "@/components/ui/MobileNav";

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
    <html lang="en" className={`${inter.variable} ${plexMono.variable} ${fraunces.variable}`}>
      <body className="flex min-h-screen flex-col bg-background text-foreground antialiased md:flex-row">
        {/* Fixed left rail on desktop; a slim top bar takes its place on mobile. */}
        <Sidebar />
        <MobileNav />

        <div className="min-w-0 flex-1 overflow-y-auto">{children}</div>
      </body>
    </html>
  );
}
