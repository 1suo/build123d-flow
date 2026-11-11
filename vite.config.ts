import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    base: env.GITHUB_PAGES === "true" ? "/build123d-flow/" : "/",
    plugins: [vue()],
    optimizeDeps: {
      exclude: ["@vue-flow/core"],
    },
  };
});
