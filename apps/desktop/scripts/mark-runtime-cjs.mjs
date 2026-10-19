import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const desktopRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
for (const directory of ["dist/main", "dist/preload"]) {
  const path = resolve(desktopRoot, directory);
  await mkdir(path, { recursive: true });
  await writeFile(resolve(path, "package.json"), '{"type":"commonjs"}\n', "utf8");
}
