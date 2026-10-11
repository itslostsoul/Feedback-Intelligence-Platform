import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/ui/Sidebar";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
});

export const metadata: Metadata = {
  title: "Feedback Intelligence | Mind Flayers",
  description: "AI-Prioritized Product Impact Grid",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`dark ${inter.variable} ${plexMono.variable}`}>
      <body className="flex min-h-screen bg-background text-foreground antialiased">
        {/* The Sidebar stays fixed on the left */}
        <Sidebar />

        {/* The main dashboard content takes up the rest of the space */}
        <div className="flex-1 overflow-y-auto">{children}</div>
      </body>
    </html>
  );
}
