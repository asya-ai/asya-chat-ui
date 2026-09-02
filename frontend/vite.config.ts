import path from "path"
import { defineConfig } from "vite"
import react, { reactCompilerPreset } from "@vitejs/plugin-react"
import babel from "@rolldown/plugin-babel"
import tailwindcss from "@tailwindcss/vite"
import { VitePWA } from "vite-plugin-pwa"

// https://vite.dev/config/
const usePolling =
  process.env.VITE_FORCE_POLLING === "true" ||
  process.env.CHOKIDAR_USEPOLLING === "1" ||
  process.env.WATCHPACK_POLLING === "true"

export default defineConfig({
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] }),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: [
        "favicon.svg",
        "apple-touch-icon.png",
        "logo_chat.svg",
        "pwa-192x192.png",
        "pwa-512x512.png",
      ],
      manifest: {
        name: "Chat UI",
        short_name: "Chat UI",
        description:
          "ChatUI by asya.ai - secure, multi-model AI chat with tools, sharing, and organization controls.",
        theme_color: "#f5f3f2",
        background_color: "#f5f3f2",
        display: "standalone",
        orientation: "any",
        start_url: "/",
        scope: "/",
        lang: "en",
        icons: [
          {
            src: "pwa-192x192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
          },
          {
            src: "pwa-512x512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//],
        // App requires network for API; precache only the SPA shell (+ includeAssets icons).
        // Hashed /assets/* rely on nginx immutable headers instead of SW install bulk fetch.
        globPatterns: ["index.html"],
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
            handler: "NetworkOnly",
          },
        ],
      },
      devOptions: {
        enabled: true,
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
