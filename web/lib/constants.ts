// Layout numbers match scripts/cut.py so the live preview matches the render.
export const W = 1080, H = 1920, TOP = 730, K = 0.5;
export const CAP_Y: Record<string, number> = { fill: 1380, blur: 1460, facecam: 1120, react: 1560, split: 960 };
export const CAP_PX = 118 * 0.786, TITLE_PX = 80 * 0.786;
/** Title box centre per layout, as cut.py TITLE_Y (react uses TOP, split its own outline at the top). */
export const TITLE_Y: Record<string, number> = { facecam: Math.floor(1920 * 0.38), fill: 300, blur: 380 }; // libass sizes as rendered (LIBASS_SCALE in cut.py)

export const LAYOUTS: [string, string, string][] = [
  ["fill", "Fill", "Game fills the frame"],
  ["blur", "Fit", "Whole frame over a blur"],
  ["react", "Split", "Webcam on top, game below, title on the seam"],
  ["facecam", "Gameplay", "Small webcam on top, more game"],
  ["split", "Brainrot", "Clip on top, Subway Surfers / ramps below to hold attention"],
];
/** Friendly names for the videos in fillers/. */
export const fillerName = (n: string) =>
  ({ subway_vertical: "Subway Surfers", subway_wide: "Subway Surfers (wide)", gta_ramp: "GTA ramps", random: "Random" } as Record<string, string>)[n]
  || n.replace(/[_-]+/g, " ").replace(/^\w/, c => c.toUpperCase());
export const LNAME: Record<string, string> = Object.fromEntries(LAYOUTS.map(([k, n]) => [k, n]));

// hook -> clip transitions (names and lengths as cut.py TRANSITIONS); blended ones overlap hook and clip
export const BLEND: Record<string, number> = { zoom: 0.35, whip: 0.3, slide: 0.3, fade: 0.45, pixel: 0.4, circle: 0.4, dissolve: 0.35 };
export const WHOOSH = new Set(["zoom", "whip", "slide", "pixel"]);
export const TRANS_LIST: [string, string, string][] = [
  ["zoom", "Zoom", "Pushes into the clip · whoosh"], ["whip", "Whip", "Motion-blur swipe · whoosh"],
  ["slide", "Slide up", "The clip pushes up · whoosh"], ["fade", "Dip to black", "Quick fade through black"],
  ["pixel", "Pixelate", "Pixels out and back · whoosh"], ["circle", "Circle", "A circle opens onto the clip"],
  ["dissolve", "Dissolve", "Grainy crossfade"], ["flash", "Flash", "White flash, hard cut"],
  ["cut", "Cut", "Hard cut"], ["rewind", "Rewind", "Plays backwards to the start"],
];
export const transName = (k: string) => (TRANS_LIST.find(x => x[0] === k) || [k, k])[1];

export const PALETTE = ["#e9c46a", "#a3e36e", "#7cc4ff", "#ff9c8a", "#c9a2ff", "#6fe3cf"];
export const NAMES = ["Tomas", "James", "Ava", "Maya", "Leo", "Nora", "Ines", "Hugo"];
export const AGENT_COLOR = "#e0866a";
