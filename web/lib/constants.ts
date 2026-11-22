// Layout numbers match scripts/cut.py so the live preview matches the render.
export const W = 1080, H = 1920, TOP = 730, K = 0.5;
export const CAP_Y: Record<string, number> = { fill: 1380, blur: 1460, facecam: 1120, react: 1560, split: 960, track: 1470 };
/** Focus layout (cut.py FOCUS_Y, FOCUS_H): the 1080x1350 window's top edge and height. */
export const FOCUS_Y = 300, FOCUS_H = 1350;
export const CAP_PX = 118 * 0.786, TITLE_PX = 80 * 0.786;
/** Title box centre per layout, as cut.py TITLE_Y (react uses TOP, split its own outline at the top). */
export const TITLE_Y: Record<string, number> = { facecam: Math.floor(1920 * 0.38), fill: 300, blur: 380, track: 300 }; // libass sizes as rendered (LIBASS_SCALE in cut.py)

export const LAYOUTS: [string, string, string][] = [
  ["fill", "Fill", "Game fills the frame"],
  ["track", "Focus", "Big window on the face or the action, over a blur"],
  ["blur", "Fit", "Whole frame over a blur"],
  ["react", "Split", "Webcam on top, game below, title on the seam"],
];
export const LNAME: Record<string, string> = Object.fromEntries(LAYOUTS.map(([k, n]) => [k, n]));

// hook -> clip transition lengths as cut.py TRANSITIONS (renders always use zoom; older clips may have another)
export const BLEND: Record<string, number> = { zoom: 0.35, whip: 0.3, slide: 0.3, fade: 0.45, pixel: 0.4, circle: 0.4, dissolve: 0.35 };
export const WHOOSH = new Set(["zoom", "whip", "slide", "pixel"]);

