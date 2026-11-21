import type { ReactNode } from "react";

const S = ({ children, fill, w = 1.8 }: { children: ReactNode; fill?: boolean; w?: number }) => (
  <svg viewBox="0 0 24 24" fill={fill ? "currentColor" : "none"} stroke={fill ? undefined : "currentColor"} strokeWidth={fill ? undefined : w}>{children}</svg>
);

export const I = {
  plus: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14" /></svg>,
  chevDown: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg>,
  chevRight: <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m9 6 6 6-6 6" /></svg>,
  check: <svg className="ck" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="m5 12 5 5 9-10" /></svg>,
  phone: <S><rect x="7" y="3" width="10" height="18" rx="2.5" /></S>,
  frame: <S><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" /><rect x="9" y="9" width="6" height="6" rx="1" /></S>,
  track: <S><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" /><circle cx="12" cy="10.5" r="2.5" /><path d="M8 17c.8-1.8 2.3-2.7 4-2.7s3.2.9 4 2.7" /></S>,
  bolt: <S><path d="M13 2 4 14h7l-1 8 9-12h-7z" /></S>,
  sparkle: <S fill><path d="M12 2.5c.6 3.9 2.4 5.8 6.3 6.4-3.9.6-5.7 2.5-6.3 6.4-.6-3.9-2.4-5.8-6.3-6.4 3.9-.6 5.7-2.5 6.3-6.4ZM18.5 14c.3 2 1.2 2.9 3.2 3.2-2 .3-2.9 1.2-3.2 3.2-.3-2-1.2-2.9-3.2-3.2 2-.3 2.9-1.2 3.2-3.2Z" /></S>,
  cc: <S><rect x="3" y="5" width="18" height="14" rx="3.5" /><path d="M10.5 10.2a2.2 2.2 0 1 0 0 3.6M16.5 10.2a2.2 2.2 0 1 0 0 3.6" /></S>,
  text: <S w={1.9}><path d="M5 6V4h14v2M12 4v16M9 20h6" /></S>,
  layout: <S><rect x="6" y="2.5" width="12" height="19" rx="2.5" /><path d="M6 10h12" /></S>,
  transitions: <S><path d="M4 5c6 0 10 14 16 14M4 19c6 0 10-14 16-14M4 5v14M20 5v14" /></S>,
  wave: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2" /></svg>,
  scissors: <S><circle cx="6" cy="7" r="3" /><circle cx="6" cy="17" r="3" /><path d="M8.5 8.5 20 18M8.5 15.5 20 6" /></S>,
  upload: <S><path d="M12 15V3M7 8l5-5 5 5M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" /></S>,
  hide: <S><rect x="3" y="5" width="18" height="14" rx="3" /><path d="m9 11 3 3 3-3" /></S>,
  split: <S><path d="M9 4H6v16h3M15 4h3v16h-3M12 3v18" strokeDasharray="2 2.4" /></S>,
  trash: <S><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6" /></S>,
  volume: <S><path d="M4 9v6h4l5 4V5L8 9z" /><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11" /></S>,
  undo: <S><path d="M9 14 4 9l5-5" /><path d="M4 9h11a5 5 0 0 1 0 10h-3" /></S>,
  prev: <S fill><path d="M6 5h2.5v14H6zM20 5v14L9.5 12z" /></S>,
  next: <S fill><path d="M15.5 5H18v14h-2.5zM4 5v14l10.5-7z" /></S>,
  play: <S fill><path d="M7 4.5v15l12.5-7.5z" /></S>,
  pause: <S fill><path d="M6.5 4h4v16h-4zM13.5 4h4v16h-4z" /></S>,
  zoomOut: <S><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4M8 11h6" /></S>,
  zoomIn: <S><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4M8 11h6M11 8v6" /></S>,
  add: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" /></svg>,
};

export const LAYOUT_ICON: Record<string, ReactNode> = {
  fill: <S><path d="M9 4H4v5M15 4h5v5M20 15v5h-5M4 15v5h5M4 4l6 6M20 4l-6 6M20 20l-6-6M4 20l6-6" /></S>,
  blur: <S><path d="M4 9h5V4M20 9h-5V4M20 15h-5v5M4 15h5v5M4 4l5 5M20 4l-5 5M20 20l-5-5M4 20l5-5" /></S>,
  react: <S><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M4 11h16" /></S>,
  facecam: <S><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M4 9h16" /></S>,
  split: <S><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M4 12h16M8 16l2 2 2-2 2 2 2-2" /></S>,
};
