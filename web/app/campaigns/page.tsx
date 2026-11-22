import type { Metadata } from "next";
import Campaigns from "@/components/Campaigns";

export const metadata: Metadata = { title: "Campaigns · Clip Desk", description: "Paid clipping campaigns: clips made for each one, what's posted, views and submissions" };

export default function Page() {
  return <Campaigns />;
}
