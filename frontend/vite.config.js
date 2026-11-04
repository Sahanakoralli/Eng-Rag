import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev (npm run dev) API calls are forwarded to the FastAPI server, so no CORS setup is needed.
const API = "http://127.0.0.1:8000";
const proxy = Object.fromEntries(
  ["/auth", "/files", "/ask", "/history", "/sessions", "/health", "/search"].map((p) => [p, API])
);

export default defineConfig({ plugins: [react()], server: { port: 5173, proxy } });