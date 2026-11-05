/**
 * Script de synchronisation des dépendances front-end embarquées (vendor/)
 * Copie les bundles depuis `node_modules/` vers `src/batch_filigrane/static/vendor/`,
 * convertit les bundles ES Module de PDF.js (v5/v6+) en scripts classiques compatibles
 * à la fois avec `https://` (GitHub Pages), `http://` (FastAPI) et `file://` (ouverture locale directe),
 * nettoie les références `sourceMappingURL` et vérifie la syntaxe JS via `node:vm`.
 *
 * Exécuté localement via `npm run sync-vendor` ou automatiquement en CI sur les PR Dependabot.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(__dirname, "..");
const VENDOR_DIR = path.join(ROOT_DIR, "src", "batch_filigrane", "static", "vendor");

const VENDOR_FILES = [
  {
    name: "pdfjs-dist (core bundle compatible file:// & https://)",
    src: "node_modules/pdfjs-dist/build/pdf.min.mjs",
    dest: "pdf.min.js",
    convertEsmToClassicScript: true,
  },
  {
    name: "pdfjs-dist (worker bundle compatible file:// & https://)",
    src: "node_modules/pdfjs-dist/build/pdf.worker.min.mjs",
    dest: "pdf.worker.min.js",
    convertEsmToClassicScript: true,
  },
  {
    name: "jspdf (UMD bundle)",
    src: "node_modules/jspdf/dist/jspdf.umd.min.js",
    dest: "jspdf.umd.min.js",
    convertEsmToClassicScript: false,
  },
  {
    name: "jszip (UMD bundle)",
    src: "node_modules/jszip/dist/jszip.min.js",
    dest: "jszip.min.js",
    convertEsmToClassicScript: false,
  },
  {
    name: "jsqr (UMD bundle)",
    src: "node_modules/jsqr/dist/jsQR.js",
    dest: "jsQR.min.js",
    convertEsmToClassicScript: false,
  },
];

// Fichiers .mjs obsolètes à nettoyer
const OBSOLETE_FILES = ["pdf.min.mjs", "pdf.worker.min.mjs"];

fs.mkdirSync(VENDOR_DIR, { recursive: true });

for (const obs of OBSOLETE_FILES) {
  const obsPath = path.join(VENDOR_DIR, obs);
  if (fs.existsSync(obsPath)) {
    fs.unlinkSync(obsPath);
    console.log(`[cleanup] Supprimé fichier .mjs : ${obs}`);
  }
}

for (const item of VENDOR_FILES) {
  const srcPath = path.join(ROOT_DIR, item.src);
  const destPath = path.join(VENDOR_DIR, item.dest);

  if (!fs.existsSync(srcPath)) {
    throw new Error(`Fichier source introuvable : ${srcPath}. Exécutez d'abord 'npm install'.`);
  }

  let content = fs.readFileSync(srcPath, "utf8");

  // 1. Suppression des commentaires sourceMappingURL pour éviter les requêtes 404 dans DevTools
  content = content.replace(/\/\/# sourceMappingURL=.*$/gm, "");

  // 2. Durcissement de sécurité défensif (neutralisation d'isEvalSupported si présent)
  content = content.replace(
    /function isEvalSupported\(\)\{try\{new Function\(""\);return!0\}catch\{return!1\}\}/g,
    "function isEvalSupported(){return!1}"
  );

  // 3. Conversion ESM -> Script classique IIFE pour PDF.js v5/v6+ (expose déjà globalThis.pdfjsLib et globalThis.pdfjsWorker)
  // Permet le chargement via <script src="./vendor/pdf.min.js"> même en ouvrant index.html en file:// sans serveur !
  if (item.convertEsmToClassicScript) {
    content = content
      .replace(
        /\bimport\.meta\.url\b/g,
        '(globalThis.location && globalThis.location.href ? globalThis.location.href : "file:///")'
      )
      .replace(/export\s*\{[^}]+\}\s*;?\s*$/, "");
    content = `(()=>{\n${content}\n})();\n`;
  }

  // 4. Vérification syntaxique stricte en tant que script JS classique (non-module)
  new vm.Script(content, { filename: item.dest });

  fs.writeFileSync(destPath, content, "utf8");
  const sizeKb = (Buffer.byteLength(content, "utf8") / 1024).toFixed(1);
  console.log(`[sync-vendor] ${item.name} -> ${item.dest} (${sizeKb} Ko) [Syntaxe OK]`);
}

console.log("[sync-vendor] Synchronisation terminée avec succès.");
