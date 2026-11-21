import type { Metadata } from "next";
import Home from "@/components/Home";

export const metadata: Metadata = { title: "Clip Desk", description: "Extract, watch live, edit and queue GTA 6 clips" };

export default function Page() {
  return <Home />;
}
