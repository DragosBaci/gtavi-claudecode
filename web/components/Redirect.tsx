"use client";
import { useEffect } from "react";

/** Old page addresses (/extract, /picks) now live on the Make page. */
export default function Redirect({ to }: { to: string }) {
  useEffect(() => { location.replace(to + location.search + location.hash); }, [to]);
  return null;
}
