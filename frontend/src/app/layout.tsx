import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TradeFlow AI — Crypto Signal Scanner",
  description: "Real-time crypto market scanner with AI-powered trading signals",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#0a0e1a] text-slate-100 antialiased">
        {children}
      </body>
    </html>
  );
}
