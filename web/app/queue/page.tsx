import type { Metadata } from "next";
import Queue from "@/components/Queue";

export const metadata: Metadata = { title: "Queue · Clip Desk", description: "The posting queue" };

export default function Page() {
  return <Queue />;
}
