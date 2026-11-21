import type { NextConfig } from "next";

// Dev: `npm run dev` proxies the Python editor server (scripts/editor.py, port 8766).
// Build: `npm run build` writes a static export to out/, which editor.py serves itself.
const API = process.env.EDITOR_API || "http://127.0.0.1:8766";
const isExport = process.env.NEXT_OUTPUT === "export";

const config: NextConfig = isExport
  ? { output: "export", images: { unoptimized: true } }
  : {
      async rewrites() {
        return ["api", "media", "fonts", "fillers"].map(p => ({ source: `/${p}/:path*`, destination: `${API}/${p}/:path*` }));
      },
    };

export default config;
