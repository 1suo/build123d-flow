import { createApp } from "vue";
import PrimeVue from "primevue/config";
import "primeicons/primeicons.css";
import Aura from "@primeuix/themes/aura";
import Tooltip from "primevue/tooltip";
import App from "./App.vue";
import "./style.css";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";

if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`)
      .catch((error) => console.warn("Offline asset cache unavailable:", error));
  });
}

createApp(App)
  .directive("tooltip", Tooltip)
  .use(PrimeVue, {
    theme: {
      preset: Aura,
      options: {
        darkModeSelector: ".app-shell",
        cssLayer: false
      }
    },
    inputVariant: "filled"
  })
  .mount("#app");
