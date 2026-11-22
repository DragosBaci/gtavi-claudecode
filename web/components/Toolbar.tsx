"use client";
import { resetEdits } from "@/lib/actions";
import { useEd } from "@/lib/store";
import { I } from "./Icons";

/** Top center: Reset. Layout, webcam and hook live in the side tabs. */
export default function Toolbar() {
  const S = useEd(s => s.S), P = useEd(s => s.P);
  const dirty = !!(S && P && JSON.stringify(S) !== JSON.stringify(P.state));
  return (
    <div id="toolbar">
      <button className="tb" id="resetBtn" disabled={!dirty} onClick={resetEdits}
        title={P?.rendered ? "Back to the last render, before your edits. ⌘Z brings them back." : "Back to the original pick, before your edits. ⌘Z brings them back."}>
        {I.undo}<span>Reset</span>
      </button>
    </div>
  );
}
