import { create } from "zustand";
import type { ClipGroup, ClipState, Group, Mode, Project, Range, RenderStatus, Tab, Unit } from "./types";

export interface EditorState {
  P: Project | null;
  S: ClipState | null;
  units: Unit[];
  groups: Group[];
  clips: ClipGroup[];
  sel: Range | null;
  mode: Mode;
  tab: Tab | null;
  menu: "layout" | "clip" | null;
  playing: boolean;
  muted: boolean;
  zoom: number;
  tlHidden: boolean;
  undo: string[];
  restored: boolean;
  curK: number;
  loading: string | null;
  strip: { url: string; n: number; tile_w: number; tile_h: number } | null;
  wave: { hop: number; peaks: number[] } | null;
  render: { id: string; status: RenderStatus } | null;
  lastRender: RenderStatus | null;
  toast: { msg: string; id: number } | null;
  crop: { box: [number, number, number, number]; aspect: string } | null;
  version: number;   // bumps on every edit, for canvases that redraw on change
}

export const useEd = create<EditorState>(() => ({
  P: null, S: null, units: [], groups: [], clips: [], sel: null, mode: "edit", tab: null, menu: null,
  playing: false, muted: false, zoom: 1, tlHidden: false, undo: [], restored: false, curK: -1, loading: "Loading…",
  strip: null, wave: null, render: null, lastRender: null, toast: null,
  crop: null, version: 0,
}));

export const ed = () => useEd.getState();
export const setEd = useEd.setState;
