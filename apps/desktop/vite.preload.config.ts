import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const desktopRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  build: {
    outDir: resolve(desktopRoot, "dist/preload"),
    emptyOutDir: false,
    lib: {
      entry: resolve(desktopRoot, "src/preload/entry.ts"),
      formats: ["cjs"],
      fileName: () => "entry.cjs",
    },
    rollupOptions: {
      external: ["electron"],
    },
  },
});
