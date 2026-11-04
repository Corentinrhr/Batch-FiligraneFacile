/**
 * Moteur de filigrane 100% Navigateur (Client-Side) pour GitHub Pages / Exécution Hors-Ligne.
 *
 * Portage JavaScript/Canvas fidèle de BOPdfDocumentTemplate.java & DFFilter.java
 * (https://github.com/MTES-MCT/dossierfacile-backend).
 *
 * Sécurité & Robustesse :
 * - Aucun chargement CDN externe (100% local depuis ./vendor/)
 * - Désactivation explicite de eval dans PDF.js (isEvalSupported: false — protection CVE-2024-4367)
 * - Traitement page par page en flux (libération immédiate des Canvas pour éviter la saturation RAM)
 * - Mise à l'échelle proportionnelle au DPI (référence A4150 = 1240px)
 */

window.BrowserWatermarkEngine = (() => {
  const A4_WIDTH_PT = 595.27563;
  const A4_HEIGHT_PT = 841.8898;
  const RATIO_PDF = A4_HEIGHT_PT / A4_WIDTH_PT;
  const REFERENCE_A4150_WIDTH = 1240.0;
  const MAX_PDF_PAGES = 200;

  const PAGE_DIMENSIONS = {
    128: { width: 1058, height: 1496, dpi: 128 },
    150: { width: 1240, height: 1754, dpi: 150 },
    220: { width: 1819, height: 2573, dpi: 220 },
    300: { width: 2480, height: 3508, dpi: 300 },
  };

  const OFFICIAL_COLORS = [
    [64, 64, 64, 255],
    [32, 32, 32, 220],
    [0, 0, 0, 110],
    [0, 0, 91, 170],
    [255, 0, 0, 170],
  ];
  const DARK_GRAY = [64, 64, 64, 255];

  let libsLoadedPromise = null;

  function loadLocalScript(localSrc) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = localSrc;
      s.async = true;
      s.onload = resolve;
      s.onerror = () => reject(new Error(`Impossible de charger la librairie locale ${localSrc}`));
      document.head.appendChild(s);
    });
  }

  async function ensureLibrariesLoaded() {
    if (libsLoadedPromise) return libsLoadedPromise;
    libsLoadedPromise = (async () => {
      await Promise.all([
        window.pdfjsLib ? Promise.resolve() : loadLocalScript("./vendor/pdf.min.js"),
        window.jspdf ? Promise.resolve() : loadLocalScript("./vendor/jspdf.umd.min.js"),
        window.JSZip ? Promise.resolve() : loadLocalScript("./vendor/jszip.min.js"),
        window.jsQR
          ? Promise.resolve()
          : loadLocalScript("./vendor/jsQR.min.js").catch(() => null),
      ]);
      if (window.pdfjsLib && window.pdfjsLib.GlobalWorkerOptions) {
        window.pdfjsLib.GlobalWorkerOptions.workerSrc = "./vendor/pdf.worker.min.js";
      }
    })();
    return libsLoadedPromise;
  }

  function releaseCanvas(canvas) {
    if (canvas) {
      canvas.width = 0;
      canvas.height = 0;
    }
  }

  function randomUniform(min, max) {
    return min + Math.random() * (max - min);
  }

  function randomInt(min, maxInclusive) {
    return Math.floor(min + Math.random() * (maxInclusive - min + 1));
  }

  function getScale(pageWidth, pageHeight, maxPageWidth) {
    const ratioImage = pageHeight / pageWidth;
    const dimWidth = ratioImage < RATIO_PDF ? pageWidth : pageHeight / RATIO_PDF;
    return dimWidth < maxPageWidth ? 1.0 : maxPageWidth / pageWidth;
  }

  function fitImageToPage(srcCanvas, maxPage) {
    const w = srcCanvas.width;
    const h = srcCanvas.height;
    const ratioImage = h / w;

    const dimW = ratioImage < RATIO_PDF ? w : Math.floor(h / RATIO_PDF);
    const dimH = ratioImage < RATIO_PDF ? Math.floor(w * RATIO_PDF) : h;

    const scale = dimW < maxPage.width ? 1.0 : maxPage.width / dimW;
    const resultW = Math.max(1, Math.floor(scale * dimW));
    const resultH = Math.max(1, Math.floor(scale * dimH));

    const scaledW = Math.max(1, Math.round(scale * w));
    const scaledH = Math.max(1, Math.round(scale * h));
    const offsetX = Math.round((scale * (dimW - w)) / 2);
    const offsetY = Math.round((scale * (dimH - h)) / 2);

    const outCanvas = document.createElement("canvas");
    outCanvas.width = resultW;
    outCanvas.height = resultH;
    const ctx = outCanvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, resultW, resultH);
    ctx.drawImage(srcCanvas, offsetX, offsetY, scaledW, scaledH);
    return outCanvas;
  }

  function applyDfDistortionFilter(srcCanvas, xFreq = 12, yFreq = 8, maxDist = 28) {
    const w = srcCanvas.width;
    const h = srcCanvas.height;
    const srcCtx = srcCanvas.getContext("2d");
    const srcData = srcCtx.getImageData(0, 0, w, h);
    const srcPixels = srcData.data;

    const dstCanvas = document.createElement("canvas");
    dstCanvas.width = w;
    dstCanvas.height = h;
    const dstCtx = dstCanvas.getContext("2d");
    const dstData = dstCtx.createImageData(w, h);
    const dstPixels = dstData.data;

    const rSqTable = new Float32Array(w);
    for (let x = 0; x < w; x++) {
      const r = Math.sin((x * xFreq) / w);
      rSqTable[x] = r * r;
    }

    for (let y = 0; y < h; y++) {
      const ySin = maxDist * Math.sin((y * yFreq) / h);
      const rowOffset = y * w;
      for (let x = 0; x < w; x++) {
        let srcY = Math.round(y + ySin * rSqTable[x]);
        if (srcY < 0) srcY = 0;
        else if (srcY >= h) srcY = h - 1;

        const srcIdx = (srcY * w + x) * 4;
        const dstIdx = (rowOffset + x) * 4;
        dstPixels[dstIdx] = srcPixels[srcIdx];
        dstPixels[dstIdx + 1] = srcPixels[srcIdx + 1];
        dstPixels[dstIdx + 2] = srcPixels[srcIdx + 2];
        dstPixels[dstIdx + 3] = srcPixels[srcIdx + 3];
      }
    }

    dstCtx.putImageData(dstData, 0, 0);
    return dstCanvas;
  }

  function detectQrCodes(pageCanvas) {
    if (!window.jsQR) return [];
    try {
      const ctx = pageCanvas.getContext("2d");
      const imgData = ctx.getImageData(0, 0, pageCanvas.width, pageCanvas.height);
      const code = window.jsQR(imgData.data, imgData.width, imgData.height);
      if (!code || !code.location) return [];
      const loc = code.location;
      const xs = [loc.topLeftCorner.x, loc.topRightCorner.x, loc.bottomLeftCorner.x, loc.bottomRightCorner.x];
      const ys = [loc.topLeftCorner.y, loc.topRightCorner.y, loc.bottomLeftCorner.y, loc.bottomRightCorner.y];
      const xMin = Math.max(0, Math.floor(Math.min(...xs)));
      const yMin = Math.max(0, Math.floor(Math.min(...ys)));
      const xMax = Math.ceil(Math.max(...xs));
      const yMax = Math.ceil(Math.max(...ys));
      return [{ x: xMin, y: yMin, w: xMax - xMin, h: yMax - yMin }];
    } catch {
      return [];
    }
  }

  function applyWatermark(pageCanvas, watermarkText, options) {
    const cleaned = (watermarkText || "").trim();
    const watermarkToApply = cleaned ? `${cleaned}   ` : " https://filigrane.beta.gouv.fr/   ";

    const w = pageCanvas.width;
    const h = pageCanvas.height;
    const dpiScale = w / REFERENCE_A4150_WIDTH;
    const diagonal = Math.floor(Math.sqrt(w * w + h * h));

    const wmCanvas = document.createElement("canvas");
    wmCanvas.width = diagonal;
    wmCanvas.height = diagonal;
    const wmCtx = wmCanvas.getContext("2d");

    const repeats = 1 + Math.floor(128 / Math.max(1, watermarkToApply.length));
    const repeatedWatermark = watermarkToApply.repeat(repeats);

    const layerOpacity = randomUniform(0.52, 0.6);
    const spaceBetweenText = diagonal / randomUniform(8.0, 10.0);
    const fontSize = Math.max(10, Math.round(28 * dpiScale));
    wmCtx.font = `${fontSize}px Arial, sans-serif`;

    for (let i = 1; i < 11; i++) {
      const [r, g, b, a] = options.useColors
        ? OFFICIAL_COLORS[randomInt(0, OFFICIAL_COLORS.length - 1)]
        : DARK_GRAY;
      const alpha = ((a / 255) * layerOpacity).toFixed(3);
      wmCtx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
      wmCtx.fillText(repeatedWatermark, 0, i * spaceBetweenText);
    }

    // Calque flou gaussien proportionnel au DPI (rayon 45..65 à 150 DPI, sigma = radius / 3)
    const radius = randomInt(45, 65) * dpiScale;
    const sigma = (radius / 3.0).toFixed(1);
    const blurOpacity = randomUniform(0.75, 0.95);

    const blurCanvas = document.createElement("canvas");
    blurCanvas.width = diagonal;
    blurCanvas.height = diagonal;
    const blurCtx = blurCanvas.getContext("2d");
    blurCtx.globalAlpha = blurOpacity;
    blurCtx.filter = `blur(${sigma}px)`;
    blurCtx.drawImage(wmCanvas, 0, 0);

    // Distorsion DFFilter optionnelle proportionnelle au DPI
    const scaledDistortion = Math.max(1, Math.round(28 * dpiScale));
    const bufferCanvas = options.useDistortion
      ? applyDfDistortionFilter(wmCanvas, 12, 8, scaledDistortion)
      : wmCanvas;

    // Rotation -25° autour du centre
    const rotatedCanvas = document.createElement("canvas");
    rotatedCanvas.width = diagonal;
    rotatedCanvas.height = diagonal;
    const rotCtx = rotatedCanvas.getContext("2d");
    rotCtx.translate(diagonal / 2, diagonal / 2);
    rotCtx.rotate((-25 * Math.PI) / 180);
    rotCtx.translate(-diagonal / 2, -diagonal / 2);
    rotCtx.drawImage(bufferCanvas, 0, 0);
    rotCtx.drawImage(blurCanvas, 0, 0);

    if (bufferCanvas !== wmCanvas) releaseCanvas(bufferCanvas);
    releaseCanvas(wmCanvas);
    releaseCanvas(blurCanvas);

    // Recadrage centré aux dimensions de la page
    const cropLeft = Math.floor(diagonal / 2 - w / 2);
    const cropTop = Math.floor(diagonal / 2 - h / 2);
    const croppedCanvas = document.createElement("canvas");
    croppedCanvas.width = w;
    croppedCanvas.height = h;
    const cropCtx = croppedCanvas.getContext("2d");
    cropCtx.drawImage(rotatedCanvas, cropLeft, cropTop, w, h, 0, 0, w, h);
    releaseCanvas(rotatedCanvas);

    // Détourage des QR codes (marge 20px à 150 DPI, proportionnelle au DPI)
    if (options.protectQrCodes) {
      const qrBoxes = detectQrCodes(pageCanvas);
      const border = Math.max(4, Math.round(20 * dpiScale));
      for (const box of qrBoxes) {
        cropCtx.clearRect(
          Math.max(0, box.x - border),
          Math.max(0, box.y - border),
          box.w + border * 2,
          box.h + border * 2
        );
      }
    }

    const finalCtx = pageCanvas.getContext("2d");
    finalCtx.drawImage(croppedCanvas, 0, 0);
    releaseCanvas(croppedCanvas);
    return pageCanvas;
  }

  function processAndAppendCanvasPage(rawCanvas, watermarkText, options, maxPage, doc, pageIndex, pageDataUrls) {
    const fitted = fitImageToPage(rawCanvas, maxPage);
    releaseCanvas(rawCanvas);
    const watermarked = applyWatermark(fitted, watermarkText, options);

    if (pageIndex > 0) {
      doc.addPage([A4_WIDTH_PT, A4_HEIGHT_PT], "portrait");
    }
    const jpegDataUrl = watermarked.toDataURL("image/jpeg", 0.9);
    const w = watermarked.width;
    const h = watermarked.height;
    releaseCanvas(watermarked);

    pageDataUrls.push({ dataUrl: jpegDataUrl, width: w, height: h });
    const drawnHeight = (h * A4_WIDTH_PT) / w;
    doc.addImage(jpegDataUrl, "JPEG", 0, 0, A4_WIDTH_PT, drawnHeight, undefined, "FAST");
  }

  async function watermarkSingleFile(file, watermarkText, options) {
    await ensureLibrariesLoaded();
    const maxPage = PAGE_DIMENSIONS[options.dpi] || PAGE_DIMENSIONS[150];
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({
      orientation: "portrait",
      unit: "pt",
      format: [A4_WIDTH_PT, A4_HEIGHT_PT],
      compress: true,
    });
    const pageDataUrls = [];

    const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

    if (isPdf) {
      const arrayBuf = await file.arrayBuffer();
      const loadingTask = window.pdfjsLib.getDocument({
        data: arrayBuf,
        isEvalSupported: false,
      });
      const pdf = await loadingTask.promise;
      try {
        if (pdf.numPages > MAX_PDF_PAGES) {
          throw new Error(`Le document dépasse la limite de ${MAX_PDF_PAGES} pages (${pdf.numPages} pages).`);
        }
        for (let i = 1; i <= pdf.numPages; i++) {
          const page = await pdf.getPage(i);
          const vp1 = page.getViewport({ scale: 1.0 });
          const scale = getScale(vp1.width, vp1.height, maxPage.width);
          const renderScale = Math.max(0.5, scale * 2.0);
          const viewport = page.getViewport({ scale: renderScale });

          const rawCanvas = document.createElement("canvas");
          rawCanvas.width = Math.round(viewport.width);
          rawCanvas.height = Math.round(viewport.height);
          const ctx = rawCanvas.getContext("2d");
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, rawCanvas.width, rawCanvas.height);

          await page.render({ canvasContext: ctx, viewport }).promise;
          page.cleanup();

          processAndAppendCanvasPage(rawCanvas, watermarkText, options, maxPage, doc, i - 1, pageDataUrls);
        }
        return {
          blob: doc.output("blob"),
          pageCount: pdf.numPages,
          pageDataUrls,
        };
      } finally {
        await pdf.destroy();
      }
    }

    const bitmap = await createImageBitmap(file);
    const rawCanvas = document.createElement("canvas");
    rawCanvas.width = bitmap.width;
    rawCanvas.height = bitmap.height;
    const ctx = rawCanvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, rawCanvas.width, rawCanvas.height);
    ctx.drawImage(bitmap, 0, 0);
    if (typeof bitmap.close === "function") bitmap.close();

    processAndAppendCanvasPage(rawCanvas, watermarkText, options, maxPage, doc, 0, pageDataUrls);
    return {
      blob: doc.output("blob"),
      pageCount: 1,
      pageDataUrls,
    };
  }

  function mergePagesToPdfBlob(allPagesDataUrls) {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({
      orientation: "portrait",
      unit: "pt",
      format: [A4_WIDTH_PT, A4_HEIGHT_PT],
      compress: true,
    });

    allPagesDataUrls.forEach((p, idx) => {
      if (idx > 0) doc.addPage([A4_WIDTH_PT, A4_HEIGHT_PT], "portrait");
      const drawnHeight = (p.height * A4_WIDTH_PT) / p.width;
      doc.addImage(p.dataUrl, "JPEG", 0, 0, A4_WIDTH_PT, drawnHeight, undefined, "FAST");
    });

    return doc.output("blob");
  }

  async function buildZipBlob(filesItems) {
    await ensureLibrariesLoaded();
    const zip = new window.JSZip();
    for (const item of filesItems) {
      if (item.blob) {
        zip.file(item.output_name, item.blob);
      }
    }
    return zip.generateAsync({ type: "blob", compression: "DEFLATE" });
  }

  return {
    watermarkSingleFile,
    mergePagesToPdfBlob,
    buildZipBlob,
  };
})();
