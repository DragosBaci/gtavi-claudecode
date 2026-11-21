import type { Metadata } from "next";
import Live from "@/components/Live";

export const metadata: Metadata = { title: "Live · Clip Desk", description: "Watch several streams at once and clip them" };

export default function Page() {
  return <Live />;
}
