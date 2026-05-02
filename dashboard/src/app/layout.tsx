import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LiveRecall · Dashboard",
  description: "Adaptive retrieval grounded in live visual memory.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-ink-900 text-slate-100 min-h-screen font-sans">{children}</body>
    </html>
  );
}
