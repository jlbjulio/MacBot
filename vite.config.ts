import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        configure(proxy) {
          proxy.on("proxyReq", (request) => {
            if (process.env.MACBOT_TOKEN)
              request.setHeader(
                "Authorization",
                `Bearer ${process.env.MACBOT_TOKEN}`,
              );
          });
        },
      },
    },
  },
});
