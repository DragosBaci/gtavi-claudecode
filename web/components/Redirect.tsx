"use client";
import { useEffect } from "react";

/** Old page addresses: /extract and /picks live on the Make page, /earn is Campaigns. */
export default function Redirect({ to }: { to: string }) {
  useEffect(() => { location.replace(to + location.search + location.hash); }, [to]);
  return null;
}
