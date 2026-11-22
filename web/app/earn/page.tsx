import type { Metadata } from "next";
import Earn from "@/components/Earn";

export const metadata: Metadata = { title: "Earn · Clip Desk", description: "Paid clipping campaigns: join, rules, submissions, earnings" };

export default function Page() {
  return <Earn />;
}
