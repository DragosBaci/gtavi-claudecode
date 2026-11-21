export type Range = [number, number];
export interface Word { w: string; s: number; e: number }
export interface Unit { kind: "word" | "pause"; s: number; e: number; w: string; wi?: number }
export type Group = Word[] & { until?: number };

/** The options cut.py renders with (source seconds). */
export interface ClipState {
  start: number; end: number;
  tease: Range | null; tease_saved?: Range | null;
  keep: Range[]; drop: Range[];
  cam: string; layout: string; title: string; title_style: string; captions: string;
  transition: string; tighten: number; keep_after_tease: boolean; punch: boolean; trim: boolean;
  filler?: string; split?: number;   // brainrot layout: bottom video (fillers/<name>.mp4), clip's share of the height
  filler_start?: number | null;
  title_secs?: number;                // the title disappears after this many seconds of the final clip (0 = never)       // second of the bottom video the clip starts on (previewed = rendered)
}
export interface Filler { name: string; url: string; dur: number; w: number; h: number }

export interface Rendered {
  url: string; segments: any[]; tease: Range | null; dur: number; fps: number; updated: number;
  transition?: { name: string | null; overlap: number };
}

export interface Project {
  job: string; name: string; set: string; pick: number;
  video: { url: string; offset: number; dur: number; w: number; h: number; fps: number };
  words: Word[]; state: ClipState; titles: string[]; game_x: number | null;
  rendered: Rendered | null; is_new: boolean; fillers?: Filler[];
  source: { title: string; channel: string };
}

export interface ClipGroup {
  job: string; title: string; channel: string;
  clips: { name: string; title: string; layout: string; editable: boolean }[];
  picks: { set: string; n: number; tag: string; title: string; start: number; end: number }[];
}

export interface Peer { sid: string; name: string; color: string; cursor: any; action: string | null; ghost?: boolean }
export interface RenderStatus { state: "running" | "done" | "error"; stage: string; elapsed: number; log?: string[]; output?: string; id?: string }

export type Mode = "edit" | "free" | "rendered";
export type Tab = "claude" | "captions" | "text" | "layout" | "transitions" | "hook" | "trim" | "export";
