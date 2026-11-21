import type { Metadata } from "next";
import Picks from "@/components/Picks";

export const metadata: Metadata = { title: "Picks · Clip Desk", description: "Every moment Claude picked: choose what to render" };

export default function Page() {
  return <Picks />;
}
