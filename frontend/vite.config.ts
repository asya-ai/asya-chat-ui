import path from "path"
import { defineConfig } from "vite"
import react, { reactCompilerPreset } from "@vitejs/plugin-react"
import babel from "@rolldown/plugin-babel"
import tailwindcss from "@tailwindcss/vite"

// https://vite.dev/config/
const usePolling =
  process.env.VITE_FORCE_POLLING === "true" ||
  process.env.CHOKIDAR_USEPOLLING === "1" ||
  process.env.WATCHPACK_POLLING === "true"

export default defineConfig({
  build: {
    sourcemap: true,
  },
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] }),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: usePolling
    ? {
        watch: {
          usePolling: true,
          interval: 100,
        },
      }
    : undefined,
})
