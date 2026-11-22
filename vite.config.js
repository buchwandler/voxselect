import { defineConfig } from "vite";

export default defineConfig({
  root: "src",
  clearScreen: false,
  build: {
    outDir: "../dist",
    emptyOutDir: true,
  },
  server: {
    strictPort: true,
    port: 1420,
    host: "127.0.0.1",
  },
});
