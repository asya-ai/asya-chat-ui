import path from "path"
import { createHash } from "node:crypto"
import { writeFileSync } from "node:fs"
import { defineConfig, type Plugin } from "vite"
import react, { reactCompilerPreset } from "@vitejs/plugin-react"
import babel from "@rolldown/plugin-babel"
import tailwindcss from "@tailwindcss/vite"
import { VitePWA } from "vite-plugin-pwa"

// https://vite.dev/config/
const usePolling =
  process.env.VITE_FORCE_POLLING === "true" ||
  process.env.CHOKIDAR_USEPOLLING === "1" ||
  process.env.WATCHPACK_POLLING === "true"

const chatuiBuildId =
  (process.env.CHATUI_BUILD_ID || "").trim() ||
  createHash("sha256").update(`${Date.now()}-${Math.random()}`).digest("hex").slice(0, 16)

function chatuiBuildIdPlugin(buildId: string): Plugin {
  const payload = `${JSON.stringify({ build_id: buildId })}\n`
  return {
    name: "chatui-build-id",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const pathOnly = req.url?.split("?", 1)[0]
        if (pathOnly !== "/build-id.json") {
          next()
          return
        }
        res.setHeader("Content-Type", "application/json")
        res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate")
        res.end(payload)
      })
    },
    writeBundle(options) {
      const outDir = options.dir
      if (!outDir) return
      writeFileSync(path.join(outDir, "build-id.json"), payload, "utf8")
    },
  }
}

export default defineConfig({
  define: {
    "import.meta.env.VITE_BUILD_ID": JSON.stringify(chatuiBuildId),
  },
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] }),
    tailwindcss(),
    chatuiBuildIdPlugin(chatuiBuildId),
    // Online-only app: do not cache the shell/assets. selfDestroying replaces any
    // previously installed SW, clears Cache Storage, unregisters, and reloads tabs.
    VitePWA({
      selfDestroying: true,
      registerType: "autoUpdate",
      injectRegister: "script",
      manifest: false,
      devOptions: {
        enabled: false,
      },
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
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
