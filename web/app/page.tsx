import type { Metadata } from "next";
import Extract from "@/components/Extract";

export const metadata: Metadata = { title: "Clip Desk", description: "Make GTA 6 clips: Claude picks, looks, cuts and queues the best moments" };

export default function Page() {
  return <Extract />;
}
