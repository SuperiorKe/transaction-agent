// Latin-subset entrypoints, not the unscoped ones: this UI only ever renders Latin text (English
// copy, KES amounts, +254 numbers), so the unscoped files' cyrillic/cyrillic-ext/greek/vietnamese
// @font-face blocks never match at runtime (unicode-range) -- they only cost dist/ disk and build
// time. See TODOS.md "The font bundle ships subsets this UI never renders".
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-sans/latin-700.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-600.css";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("index.html is missing #root");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
