"use client";
// A joke paywall for pranking a friend. Shows only while the desk folder has a `.prank` file (/api/health says so),
// after that many seconds on the app (default 30, counted across pages in this tab), once per browser.
// It takes nothing: no inputs, no payment, every button ends in the reveal.
import { useEffect, useState } from "react";

const SEEN = "cd-prank-seen", START = "cd-prank-start";

export default function Paywall() {
  const [on, setOn] = useState(false), [step, setStep] = useState<"wall" | "nope" | "gotcha">("wall");
  useEffect(() => {
    let seen = false;
    try { seen = localStorage.getItem(SEEN) === "1"; } catch { /* private window: show it anyway */ }
    if (seen) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    fetch("/api/health").then(r => r.json()).then(h => {
      if (h.prank == null || h.prank === false) return;
      let start = Date.now();   // when this tab first opened the app, so switching pages doesn't restart the clock
      try { start = Number(sessionStorage.getItem(START)) || start; sessionStorage.setItem(START, String(start)); } catch { /* per page then */ }
      timer = setTimeout(() => setOn(true), Math.max(0, Number(h.prank) * 1000 - (Date.now() - start)));
    }).catch(() => {});
    return () => clearTimeout(timer);
  }, []);
  if (!on) return null;
  const close = () => { try { localStorage.setItem(SEEN, "1"); } catch { /* fine */ } setOn(false); };
  return (
    <div className="pw-back">
      <div className={"pw-card" + (step === "nope" ? " shake" : "")}>
        {step !== "gotcha" ? <>
          <div className="pw-lock">🔒</div>
          <h2>Your free trial has ended</h2>
          <p className="pw-sub">Upgrade to <b>Clip Desk Pro</b> to keep clipping.</p>
          <ul>
            <li>Unlimited extractions and renders</li>
            <li>Live mode on up to 9 streams</li>
            <li>Auto-posting to TikTok, Shorts and Reels</li>
          </ul>
          <div className="pw-price"><b>$49.99</b> / month</div>
          <button className="btnx white pw-go" onClick={() => setStep("gotcha")}>Upgrade to Pro</button>
          <div className="pw-row">
            <button onClick={() => setStep("gotcha")}>Restore purchase</button>
            <button onClick={() => setStep("nope")}>Maybe later</button>
          </div>
          {step === "nope" && <p className="pw-warn">Pro is required to continue.</p>}
        </> : <>
          <div className="pw-lock">😂</div>
          <h2>Gotcha.</h2>
          <p className="pw-sub">There's no Pro. Clip Desk is free and always was.</p>
          <button className="btnx white pw-go" onClick={close}>Back to the desk</button>
        </>}
      </div>
    </div>
  );
}
