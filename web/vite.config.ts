import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import ports from "../config.json" with { type: "json" };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: ports.console_dev,
    // Bound to localhost on purpose. The control plane behind this console has
    // no authentication, so putting the console on the network would publish an
    // unauthenticated write surface with a convenient interface attached.
    host: "127.0.0.1",
  },
  preview: { port: ports.console_preview, host: "127.0.0.1" },
});
