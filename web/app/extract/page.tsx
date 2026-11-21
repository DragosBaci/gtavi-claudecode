import type { Metadata } from "next";
import Extract from "@/components/Extract";

export const metadata: Metadata = { title: "Extract · Clip Desk", description: "Extract clips from YouTube videos" };

export default function Page() {
  return <Extract />;
}
