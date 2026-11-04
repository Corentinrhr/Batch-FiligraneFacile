/**
 * Logique Frontend — Batch FiligraneFacile (100% Local)
 * Fonctionne à la fois :
 * - Avec le serveur local Python FastAPI (`uv run batch-filigrane`)
 * - En mode 100% statique dans le navigateur sur GitHub Pages (`browser_engine.js`)
 */

(() => {
  // Icônes SVG vectorielles
  const ICONS = {
    arrowRight: `<svg class="icon icon--xs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>`,
    check: `<svg class="icon icon--xs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
    alert: `<svg class="icon icon--xs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
    close: `<svg class="icon icon--sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
    retry: `<svg class="icon icon--sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`,
    dotsVertical: `<svg class="icon icon--sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="12" cy="19" r="1.2"/></svg>`,
    eye: `<svg class="icon icon--sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>`,
    download: `<svg class="icon icon--sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`,
  };

  // Éléments DOM
  const watermarkInput = document.getElementById("watermark-input");
  const charCounter = document.getElementById("char-counter");
  const presetBtns = document.querySelectorAll(".chip-btn");
  const concurrencyInput = document.getElementById("concurrency-input");
  const concurrencyVal = document.getElementById("concurrency-val");
  const dpiSelect = document.getElementById("dpi-select");
  const suffixInput = document.getElementById("suffix-input");
  const optQrCodes = document.getElementById("opt-qr-codes");
  const optColors = document.getElementById("opt-colors");
  const optDistortion = document.getElementById("opt-distortion");

  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const queueCountBadge = document.getElementById("queue-count-badge");
  const actionBar = document.getElementById("action-bar");
  const queueSummary = document.getElementById("queue-summary");
  const clearQueueBtn = document.getElementById("clear-queue-btn");
  const startBatchBtn = document.getElementById("start-batch-btn");

  const downloadDropdownWrapper = document.getElementById("download-dropdown-wrapper");
  const downloadDropdownBtn = document.getElementById("download-dropdown-btn");
  const downloadDropdownMenu = document.getElementById("download-dropdown-menu");
  const downloadAllIndividualBtn = document.getElementById("download-all-individual-btn");
  const downloadAllZipBtn = document.getElementById("download-all-zip-btn");
  const downloadMergedPdfBtn = document.getElementById("download-merged-pdf-btn");
  const downloadMergedLabel = document.getElementById("download-merged-label");
  const previewMergedPdfBtn = document.getElementById("preview-merged-pdf-btn");

  const batchProgressPanel = document.getElementById("batch-progress-panel");
  const batchStatusTitle = document.getElementById("batch-status-title");
  const batchStatsText = document.getElementById("batch-stats-text");
  const batchPercentBadge = document.getElementById("batch-percent-badge");
  const batchProgressFill = document.getElementById("batch-progress-fill");

  const emptyState = document.getElementById("empty-state");
  const filesList = document.getElementById("files-list");

  const previewModal = document.getElementById("preview-modal");
  const previewModalTitle = document.getElementById("preview-modal-title");
  const previewModalDownload = document.getElementById("preview-modal-download");
  const closeModalBtn = document.getElementById("close-modal-btn");
  const previewIframe = document.getElementById("preview-iframe");

  // État applicatif
  let stagedFiles = [];
  let activeBatch = null;
  let activeBlobUrls = [];
  let pollTimer = null;
  let isRunning = false;
  let hasPythonBackend = null;

  function createTrackedBlobUrl(blob) {
    const url = URL.createObjectURL(blob);
    activeBlobUrls.push(url);
    return url;
  }

  function revokeTrackedBlobUrls() {
    for (const url of activeBlobUrls) {
      try {
        URL.revokeObjectURL(url);
      } catch {
        // Ignorer les erreurs de révocation
      }
    }
    activeBlobUrls = [];
  }

  function cleanupActiveBatch() {
    revokeTrackedBlobUrls();
    if (activeBatch && !activeBatch.isBrowserBatch && activeBatch.id) {
      const batchIdToClean = activeBatch.id;
      fetch(`./api/batches/${encodeURIComponent(batchIdToClean)}`, {
        method: "DELETE",
        keepalive: true,
      }).catch(() => {});
    }
    activeBatch = null;
  }

  window.addEventListener("pagehide", () => {
    cleanupActiveBatch();
  });

  async function detectBackendMode() {
    if (hasPythonBackend !== null) return hasPythonBackend;
    try {
      const ctrl = new AbortController();
      const timeoutId = setTimeout(() => ctrl.abort(), 1200);
      const res = await fetch("./api/health", { signal: ctrl.signal });
      clearTimeout(timeoutId);
      if (res.ok) {
        const json = await res.json();
        hasPythonBackend = json && json.status === "ok";
      } else {
        hasPythonBackend = false;
      }
    } catch {
      hasPythonBackend = false;
    }
    return hasPythonBackend;
  }

  detectBackendMode();

  // Utilitaires
  function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return "0 o";
    const units = ["o", "Ko", "Mo", "Go"];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function escapeHtml(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function getPredictedOutputName(originalName) {
    const suffix = suffixInput.value ?? "_filigrane";
    const lastDot = originalName.lastIndexOf(".");
    const stem = lastDot > 0 ? originalName.slice(0, lastDot) : originalName;
    return `${stem}${suffix}.pdf`;
  }

  function updateCharCounter() {
    const len = watermarkInput.value.length;
    charCounter.textContent = `${len}/100`;
  }

  watermarkInput.addEventListener("input", updateCharCounter);
  updateCharCounter();

  presetBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      watermarkInput.value = btn.dataset.preset || "";
      updateCharCounter();
    });
  });

  concurrencyInput.addEventListener("input", () => {
    concurrencyVal.textContent = concurrencyInput.value;
  });

  suffixInput.addEventListener("input", () => {
    if (!activeBatch && stagedFiles.length > 0) {
      renderStagedFiles();
    }
  });

  // Gestion des menus déroulants (Bouton Télécharger + Sous-menus 3 points)
  function closeAllMenus() {
    downloadDropdownMenu.hidden = true;
    downloadDropdownWrapper.classList.remove("dropdown--open");
    downloadDropdownBtn.setAttribute("aria-expanded", "false");

    document.querySelectorAll(".file-submenu").forEach((menu) => {
      menu.hidden = true;
    });
    document.querySelectorAll(".file-menu-toggle").forEach((btn) => {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  downloadDropdownBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (downloadDropdownBtn.disabled) return;
    const isOpen = !downloadDropdownMenu.hidden;
    closeAllMenus();
    if (!isOpen) {
      downloadDropdownMenu.hidden = false;
      downloadDropdownWrapper.classList.add("dropdown--open");
      downloadDropdownBtn.setAttribute("aria-expanded", "true");
    }
  });

  document.addEventListener("click", () => {
    closeAllMenus();
  });

  // Drag & Drop et Sélection de fichiers
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fileInput.click();
    }
  });

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dropzone--dragover");
  });

  dropzone.addEventListener("dragleave", () => {
    dropzone.classList.remove("dropzone--dragover");
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dropzone--dragover");
    if (e.dataTransfer && e.dataTransfer.files) {
      addFilesToStage(Array.from(e.dataTransfer.files));
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files) {
      addFilesToStage(Array.from(e.target.files));
      fileInput.value = "";
    }
  });

  function addFilesToStage(newFiles) {
    const allowedExt = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"];
    const valid = newFiles.filter((file) => {
      const lower = file.name.toLowerCase();
      return allowedExt.some((ext) => lower.endsWith(ext));
    });

    if (valid.length === 0) return;

    if (activeBatch && !isRunning) {
      cleanupActiveBatch();
      batchProgressPanel.hidden = true;
      downloadDropdownBtn.disabled = true;
    }

    stagedFiles = stagedFiles.concat(valid);
    renderStagedFiles();
  }

  function removeStagedFile(index) {
    stagedFiles.splice(index, 1);
    renderStagedFiles();
  }

  clearQueueBtn.addEventListener("click", () => {
    if (isRunning) return;
    closeAllMenus();
    cleanupActiveBatch();
    stagedFiles = [];
    batchProgressPanel.hidden = true;
    downloadDropdownBtn.disabled = true;
    renderStagedFiles();
  });

  function renderStagedFiles() {
    const count = stagedFiles.length;
    queueCountBadge.textContent = `${count} fichier${count > 1 ? "s" : ""} sélectionné${count > 1 ? "s" : ""}`;

    if (count === 0) {
      emptyState.hidden = false;
      filesList.hidden = true;
      filesList.innerHTML = "";
      queueSummary.textContent = "Aucun document en attente";
      clearQueueBtn.disabled = true;
      startBatchBtn.disabled = true;
      downloadDropdownBtn.disabled = true;
      return;
    }

    emptyState.hidden = true;
    filesList.hidden = false;
    clearQueueBtn.disabled = false;
    startBatchBtn.disabled = false;
    downloadDropdownBtn.disabled = true;

    const totalSize = stagedFiles.reduce((acc, f) => acc + f.size, 0);
    queueSummary.textContent = `${count} document${count > 1 ? "s" : ""} prêt${count > 1 ? "s" : ""} (${formatBytes(totalSize)})`;

    filesList.innerHTML = stagedFiles
      .map((file, idx) => {
        const predictedOut = getPredictedOutputName(file.name);
        return `
          <li class="file-item">
            <div class="file-item__info">
              <div class="file-item__name-row">
                <span class="file-item__orig-name">${escapeHtml(file.name)}</span>
                <span class="file-item__arrow">${ICONS.arrowRight}</span>
                <span class="file-item__out-name">${escapeHtml(predictedOut)}</span>
              </div>
              <div class="file-item__meta">
                <span>Taille : ${formatBytes(file.size)}</span>
                <span class="badge badge--neutral">Prêt à traiter</span>
              </div>
            </div>
            <div class="file-item__actions">
              <button
                type="button"
                class="icon-action-btn remove-staged-btn"
                data-idx="${idx}"
                title="Retirer de la liste"
                aria-label="Retirer de la liste"
              >
                ${ICONS.close}
              </button>
            </div>
          </li>
        `;
      })
      .join("");

    filesList.querySelectorAll(".remove-staged-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        removeStagedFile(Number(btn.dataset.idx));
      });
    });
  }

  // Exécution 100% navigateur (GitHub Pages)
  async function runClientSideBatch(filesToProcess) {
    const wmText = watermarkInput.value.trim();
    const concurrency = Math.max(1, Math.min(16, Number(concurrencyInput.value) || 4));
    const suffix = suffixInput.value || "_filigrane";
    const options = {
      dpi: Number(dpiSelect.value) || 150,
      useColors: optColors.checked,
      useDistortion: optDistortion.checked,
      protectQrCodes: optQrCodes.checked,
    };

    const usedNames = {};
    const items = filesToProcess.map((file, idx) => {
      const baseOut = getPredictedOutputName(file.name);
      const lower = baseOut.toLowerCase();
      let finalOut = baseOut;
      if (usedNames[lower]) {
        usedNames[lower] += 1;
        const stem = baseOut.slice(0, -4);
        finalOut = `${stem}_${usedNames[lower]}.pdf`;
      } else {
        usedNames[lower] = 1;
      }

      return {
        id: `local_${idx}`,
        index: idx,
        file,
        original_name: file.name,
        output_name: finalOut,
        input_size: file.size,
        output_size: 0,
        page_count: 0,
        status: "queued",
        error_message: null,
        duration_ms: null,
        download_url: "#",
        preview_url: "#",
        blob: null,
        pageDataUrls: [],
      };
    });

    const stems = items.map((it) => {
      const d = it.original_name.lastIndexOf(".");
      return d > 0 ? it.original_name.slice(0, d) : it.original_name;
    });
    const mergedFilename =
      stems.length <= 3
        ? `${stems.join("_")}${suffix}.pdf`
        : `documents_fusionnes_${stems.length}${suffix}.pdf`;

    const batchState = {
      id: "browser_batch",
      isBrowserBatch: true,
      total: items.length,
      completed: 0,
      failed: 0,
      active: 0,
      total_pages: 0,
      percent: 0,
      is_finished: false,
      zip_url: "#",
      merged_url: "#",
      merged_preview_url: "#",
      merged_filename: mergedFilename,
      files: items,
    };

    activeBatch = batchState;
    stagedFiles = [];
    renderActiveBatch(batchState);

    function syncProgress() {
      const done = batchState.completed + batchState.failed;
      batchState.percent = batchState.total > 0 ? Math.round((done / batchState.total) * 100) : 100;
      batchState.is_finished = done === batchState.total;
      renderActiveBatch(batchState);
    }

    let cursor = 0;
    async function worker() {
      while (cursor < items.length) {
        const currentIndex = cursor++;
        const item = items[currentIndex];
        item.status = "processing";
        batchState.active += 1;
        syncProgress();

        const t0 = performance.now();
        try {
          const result = await window.BrowserWatermarkEngine.watermarkSingleFile(
            item.file,
            wmText,
            options
          );
          const blobUrl = createTrackedBlobUrl(result.blob);
          item.file = null;
          item.blob = result.blob;
          item.pageDataUrls = result.pageDataUrls;
          item.output_size = result.blob.size;
          item.page_count = result.pageCount;
          item.download_url = blobUrl;
          item.preview_url = blobUrl;
          item.status = "completed";
          batchState.completed += 1;
          batchState.total_pages += result.pageCount;
        } catch (err) {
          item.status = "error";
          item.error_message = err.message || String(err);
          batchState.failed += 1;
        } finally {
          item.duration_ms = Math.round(performance.now() - t0);
          batchState.active -= 1;
          syncProgress();
        }
      }
    }

    const workers = Array.from({ length: Math.min(concurrency, items.length) }, () => worker());
    await Promise.all(workers);

    if (batchState.completed > 0) {
      const completedItems = items.filter((it) => it.status === "completed");
      const allPageDataUrls = completedItems.flatMap((it) => it.pageDataUrls);
      const mergedBlob = window.BrowserWatermarkEngine.mergePagesToPdfBlob(allPageDataUrls);
      for (const it of completedItems) {
        it.pageDataUrls = [];
      }
      const mergedBlobUrl = createTrackedBlobUrl(mergedBlob);
      batchState.merged_url = mergedBlobUrl;
      batchState.merged_preview_url = mergedBlobUrl;

      const zipBlob = await window.BrowserWatermarkEngine.buildZipBlob(completedItems);
      batchState.zip_url = createTrackedBlobUrl(zipBlob);
    }

    isRunning = false;
    syncProgress();
  }

  // Lancement du traitement par lot
  startBatchBtn.addEventListener("click", async () => {
    if (stagedFiles.length === 0 || isRunning) return;

    cleanupActiveBatch();
    isRunning = true;
    startBatchBtn.disabled = true;
    clearQueueBtn.disabled = true;
    downloadDropdownBtn.disabled = true;
    closeAllMenus();

    batchProgressPanel.hidden = false;
    batchStatusTitle.textContent = "Lancement du traitement parallèle local...";
    batchStatsText.textContent = `0 / ${stagedFiles.length} terminés`;
    batchPercentBadge.textContent = "0%";
    batchProgressFill.style.width = "0%";

    const usePython = await detectBackendMode();
    if (!usePython) {
      await runClientSideBatch(stagedFiles.slice());
      return;
    }

    const formData = new FormData();
    stagedFiles.forEach((file) => {
      formData.append("files", file);
    });
    formData.append("watermark", watermarkInput.value.trim());
    formData.append("concurrency", concurrencyInput.value);
    formData.append("dpi", dpiSelect.value);
    formData.append("use_colors", optColors.checked ? "true" : "false");
    formData.append("use_distortion", optDistortion.checked ? "true" : "false");
    formData.append("protect_qr_codes", optQrCodes.checked ? "true" : "false");
    formData.append("suffix", suffixInput.value || "_filigrane");

    try {
      const response = await fetch("./api/batches", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: "Erreur serveur" }));
        throw new Error(err.detail || `Erreur HTTP ${response.status}`);
      }

      activeBatch = await response.json();
      stagedFiles = [];
      renderActiveBatch(activeBatch);
      startPolling(activeBatch.id);
    } catch (err) {
      batchStatusTitle.textContent = `Erreur : ${err.message}`;
      isRunning = false;
      startBatchBtn.disabled = false;
      clearQueueBtn.disabled = false;
    }
  });

  function startPolling(batchId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch(`./api/batches/${batchId}`);
        if (!res.ok) return;
        const data = await res.json();
        activeBatch = data;

        if (data.is_finished) {
          clearInterval(pollTimer);
          pollTimer = null;
          isRunning = false;
        }
        renderActiveBatch(data);
      } catch (e) {
        console.error("Erreur lors du suivi du lot :", e);
      }
    }, 250);
  }

  function getStatusBadgeHtml(status) {
    switch (status) {
      case "queued":
        return `<span class="badge badge--neutral">En file d'attente</span>`;
      case "processing":
        return `<span class="badge badge--primary"><span class="spinner"></span><span>Filigranage...</span></span>`;
      case "completed":
        return `<span class="badge badge--success">${ICONS.check}<span>Terminé</span></span>`;
      case "error":
        return `<span class="badge badge--danger">${ICONS.alert}<span>Erreur</span></span>`;
      default:
        return `<span class="badge badge--neutral">${escapeHtml(status)}</span>`;
    }
  }

  function renderActiveBatch(batch) {
    startBatchBtn.disabled = true;
    clearQueueBtn.disabled = isRunning;

    queueCountBadge.textContent = `${batch.completed} / ${batch.total} PDF généré${batch.completed > 1 ? "s" : ""}`;
    batchPercentBadge.textContent = `${batch.percent}%`;
    batchProgressFill.style.width = `${batch.percent}%`;

    const summaryStats = `${batch.completed} réussi${batch.completed > 1 ? "s" : ""}${
      batch.failed > 0 ? `, ${batch.failed} en erreur` : ""
    } sur ${batch.total} (${batch.total_pages || 0} page${(batch.total_pages || 0) > 1 ? "s" : ""} au total)`;

    batchStatsText.textContent = summaryStats;
    queueSummary.textContent = summaryStats;

    if (batch.is_finished) {
      batchStatusTitle.textContent =
        batch.failed === 0
          ? `Traitement terminé — ${batch.completed} document${batch.completed > 1 ? "s" : ""} filigrané${
              batch.completed > 1 ? "s" : ""
            } prêt${batch.completed > 1 ? "s" : ""}`
          : `Traitement terminé (${batch.completed} réussis, ${batch.failed} en échec)`;
    } else {
      batchStatusTitle.textContent = `Traitement parallèle local en cours (${batch.active} actif${
        batch.active > 1 ? "s" : ""
      })...`;
    }

    if (batch.completed > 0) {
      downloadDropdownBtn.disabled = false;
      downloadAllZipBtn.href = batch.zip_url;
      downloadAllZipBtn.setAttribute("download", `filigrane_lot_${batch.id}.zip`);
      downloadMergedPdfBtn.href = batch.merged_url;
      downloadMergedPdfBtn.setAttribute("download", batch.merged_filename || "documents_fusionnes_filigrane.pdf");
      downloadMergedLabel.textContent = `Télécharger en PDF fusionné (${batch.total_pages || batch.completed} page${
        (batch.total_pages || batch.completed) > 1 ? "s" : ""
      })`;
    } else {
      downloadDropdownBtn.disabled = true;
    }

    emptyState.hidden = true;
    filesList.hidden = false;

    filesList.innerHTML = batch.files
      .map((item) => {
        const stateClass =
          item.status === "completed"
            ? "file-item--completed"
            : item.status === "error"
            ? "file-item--error"
            : item.status !== "queued"
            ? "file-item--processing"
            : "";

        const pagesInfo =
          item.page_count > 0
            ? `<span>${item.page_count} page${item.page_count > 1 ? "s" : ""}</span>`
            : "";
        const sizeInfo =
          item.output_size > 0
            ? `<span>${formatBytes(item.input_size)} ${ICONS.arrowRight} <strong>${formatBytes(item.output_size)}</strong></span>`
            : `<span>${formatBytes(item.input_size)}</span>`;
        const durationInfo =
          item.duration_ms !== null ? `<span>${(item.duration_ms / 1000).toFixed(2)} s</span>` : "";

        const errorHtml = item.error_message
          ? `<div class="file-item__error">${escapeHtml(item.error_message)}</div>`
          : "";

        let actionsHtml = "";
        if (item.status === "completed") {
          actionsHtml = `
            <button
              type="button"
              class="icon-action-btn file-menu-toggle"
              aria-haspopup="true"
              aria-expanded="false"
              title="Actions sur ce fichier"
              aria-label="Actions sur ce fichier"
            >
              ${ICONS.dotsVertical}
            </button>
            <div class="file-submenu" role="menu" hidden>
              <button
                type="button"
                class="file-submenu__item preview-file-btn"
                role="menuitem"
                data-preview-url="${escapeHtml(item.preview_url)}"
                data-download-url="${escapeHtml(item.download_url)}"
                data-name="${escapeHtml(item.output_name)}"
              >
                ${ICONS.eye}
                <span>Aperçu</span>
              </button>
              <a
                href="${escapeHtml(item.download_url)}"
                download="${escapeHtml(item.output_name)}"
                class="file-submenu__item"
                role="menuitem"
              >
                ${ICONS.download}
                <span>Télécharger .PDF</span>
              </a>
            </div>
          `;
        } else if (item.status === "error" && !batch.isBrowserBatch) {
          actionsHtml = `
            <button
              type="button"
              class="icon-action-btn retry-file-btn"
              data-file-id="${escapeHtml(item.id)}"
              title="Réessayer"
              aria-label="Réessayer"
            >
              ${ICONS.retry}
            </button>
          `;
        }

        return `
          <li class="file-item ${stateClass}">
            <div class="file-item__info">
              <div class="file-item__name-row">
                <span class="file-item__orig-name">${escapeHtml(item.original_name)}</span>
                <span class="file-item__arrow">${ICONS.arrowRight}</span>
                <span class="file-item__out-name">${escapeHtml(item.output_name)}</span>
              </div>
              <div class="file-item__meta">
                ${getStatusBadgeHtml(item.status)}
                ${sizeInfo}
                ${pagesInfo}
                ${durationInfo}
              </div>
              ${errorHtml}
            </div>
            <div class="file-item__actions">
              ${actionsHtml}
            </div>
          </li>
        `;
      })
      .join("");

    // Sous-menu 3 points verticaux par fichier
    filesList.querySelectorAll(".file-menu-toggle").forEach((toggleBtn) => {
      toggleBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const submenu = toggleBtn.nextElementSibling;
        if (!submenu) return;
        const wasOpen = !submenu.hidden;
        closeAllMenus();
        if (!wasOpen) {
          submenu.hidden = false;
          toggleBtn.setAttribute("aria-expanded", "true");
        }
      });
    });

    filesList.querySelectorAll(".preview-file-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        closeAllMenus();
        openPreviewModal(btn.dataset.name, btn.dataset.previewUrl, btn.dataset.downloadUrl);
      });
    });

    filesList.querySelectorAll(".retry-file-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!activeBatch || activeBatch.isBrowserBatch) return;
        const fileId = btn.dataset.fileId;
        await fetch(`./api/batches/${activeBatch.id}/files/${fileId}/retry`, { method: "POST" });
        isRunning = true;
        startPolling(activeBatch.id);
      });
    });
  }

  previewMergedPdfBtn.addEventListener("click", () => {
    closeAllMenus();
    if (!activeBatch) return;
    openPreviewModal(
      activeBatch.merged_filename || "documents_fusionnes_filigrane.pdf",
      activeBatch.merged_preview_url,
      activeBatch.merged_url
    );
  });

  downloadAllZipBtn.addEventListener("click", () => {
    closeAllMenus();
  });

  downloadMergedPdfBtn.addEventListener("click", () => {
    closeAllMenus();
  });

  downloadAllIndividualBtn.addEventListener("click", async () => {
    closeAllMenus();
    if (!activeBatch) return;
    const completedFiles = activeBatch.files.filter((f) => f.status === "completed");
    if (completedFiles.length === 0) return;

    downloadAllIndividualBtn.disabled = true;

    for (let i = 0; i < completedFiles.length; i++) {
      const item = completedFiles[i];
      const link = document.createElement("a");
      link.href = item.download_url;
      link.download = item.output_name;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      await new Promise((r) => setTimeout(r, 250));
    }

    downloadAllIndividualBtn.disabled = false;
  });

  function openPreviewModal(filename, previewUrl, downloadUrl) {
    previewModalTitle.textContent = `Aperçu — ${filename}`;
    previewModalDownload.href = downloadUrl;
    previewModalDownload.setAttribute("download", filename);
    previewIframe.src = previewUrl;
    previewModal.hidden = false;
  }

  function closePreviewModal() {
    previewModal.hidden = true;
    previewIframe.src = "about:blank";
  }

  closeModalBtn.addEventListener("click", closePreviewModal);
  previewModal.addEventListener("click", (e) => {
    if (e.target === previewModal) closePreviewModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeAllMenus();
      if (!previewModal.hidden) closePreviewModal();
    }
  });
})();
