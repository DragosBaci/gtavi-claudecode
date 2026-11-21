import type { Metadata } from "next";
import "./globals.css";
import Paywall from "@/components/Paywall";

export const metadata: Metadata = { title: "Clip Editor", description: "Edit GTA 6 clips: transcript, hook, transitions, render with cut.py" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}<Paywall /></body>
    </html>
  );
}
