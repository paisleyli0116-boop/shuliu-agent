import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const desktopRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: resolve(desktopRoot, "src/renderer"),
  base: "./",
  plugins: [react()],
  build: {
    outDir: resolve(desktopRoot, "dist/renderer"),
    emptyOutDir: false,
    sourcemap: false,
  },
});
