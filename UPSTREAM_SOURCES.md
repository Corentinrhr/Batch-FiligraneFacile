# Correspondance avec le code source officiel DossierFacile / FiligraneFacile (MTES-MCT)

Ce document répertorie tous les fichiers sources officiels de l'État ([`MTES-MCT/dossierfacile-backend`](https://github.com/MTES-MCT/dossierfacile-backend) et [`MTES-MCT/Dossier-Facile-Frontend`](https://github.com/MTES-MCT/Dossier-Facile-Frontend)) dont l'algorithme a été porté dans ce projet, afin de faciliter le suivi et les mises à jour futures.

---

## 🔄 Comment vérifier si le projet officiel a évolué ?

Une commande dédiée télécharge les fichiers sources depuis les dépôts GitHub de `MTES-MCT`, compare leurs empreintes SHA-256 avec le manifeste local [`upstream/manifest.json`](./upstream/manifest.json) et affiche le **`diff` exact ligne par ligne** si l'équipe DossierFacile a modifié son algorithme :

```powershell
# Vérifier si des modifications ont eu lieu côté MTES-MCT (et afficher le diff)
uv run check-upstream

# Après avoir répercuté une modification amont dans le projet, mettre à jour la référence locale :
uv run check-upstream --sync
```

Une GitHub Action ([`.github/workflows/check-upstream.yml`](./.github/workflows/check-upstream.yml)) exécute également cette vérification automatiquement chaque semaine.

---

## 🗺️ Table de correspondance des fichiers et fonctions

| Fichier officiel (MTES-MCT) | Copie de référence locale | Fichiers portés dans ce projet | Rôle & Fonctions correspondantes |
| :--- | :--- | :--- | :--- |
| **[`BOPdfDocumentTemplate.java`](https://github.com/MTES-MCT/dossierfacile-backend/blob/main/dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/service/templates/BOPdfDocumentTemplate.java)** | [`upstream/reference/BOPdfDocumentTemplate.java`](./upstream/reference/BOPdfDocumentTemplate.java) | • [`src/batch_filigrane/watermark_engine.py`](./src/batch_filigrane/watermark_engine.py)<br>• [`src/batch_filigrane/static/browser_engine.js`](./src/batch_filigrane/static/browser_engine.js) | • `convertToImages` ➔ `convert_to_images()`<br>• `getScale` ➔ `_get_scale()`<br>• `fitImageToPage` ➔ `fit_image_to_page()`<br>• `applyWatermark` ➔ `apply_watermark()`<br>• `getGaussianBlurFilter` ➔ `ImageFilter.GaussianBlur(radius / 3.0)`<br>• `detectQRCodes` ➔ `detect_qr_codes()`<br>• `addImageAsPageToDocument` ➔ `images_to_a4_pdf()` |
| **[`DFFilter.java`](https://github.com/MTES-MCT/dossierfacile-backend/blob/main/dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/service/templates/DFFilter.java)** | [`upstream/reference/DFFilter.java`](./upstream/reference/DFFilter.java) | • [`src/batch_filigrane/watermark_engine.py`](./src/batch_filigrane/watermark_engine.py)<br>• [`src/batch_filigrane/static/browser_engine.js`](./src/batch_filigrane/static/browser_engine.js) | • `transformInverse` (`xFrequency=12`, `yFrequency=8`, `maxDistorsion=28`) ➔ `apply_df_distortion_filter()` |
| **[`PdfTemplateParameters.java`](https://github.com/MTES-MCT/dossierfacile-backend/blob/main/dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/model/PdfTemplateParameters.java)** | [`upstream/reference/PdfTemplateParameters.java`](./upstream/reference/PdfTemplateParameters.java) | • [`src/batch_filigrane/watermark_engine.py`](./src/batch_filigrane/watermark_engine.py) | • `mediaBox = PDRectangle.A4` (`595.28 x 841.89 pt`)<br>• `compressionQuality = 0.9f` (`90%`)<br>• `maxPage = PageDimension.A4150` |
| **[`PageDimension.java`](https://github.com/MTES-MCT/dossierfacile-backend/blob/main/dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/model/PageDimension.java)** | [`upstream/reference/PageDimension.java`](./upstream/reference/PageDimension.java) | • [`src/batch_filigrane/watermark_engine.py`](./src/batch_filigrane/watermark_engine.py) | • `A4128` (`1058 x 1496 @ 128 DPI`)<br>• `A4150` (`1240 x 1754 @ 150 DPI`) |
| **[`FeatureFlipping.java`](https://github.com/MTES-MCT/dossierfacile-backend/blob/main/dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/configuration/FeatureFlipping.java)** | [`upstream/reference/FeatureFlipping.java`](./upstream/reference/FeatureFlipping.java) | • [`src/batch_filigrane/watermark_engine.py`](./src/batch_filigrane/watermark_engine.py) | • `shouldUseColors()` ➔ `WatermarkOptions.use_colors`<br>• `shouldUseDistortion()` ➔ `WatermarkOptions.use_distortion` |
| **[`WatermarkingPage.vue`](https://github.com/MTES-MCT/Dossier-Facile-Frontend/blob/main/watermark/src/views/WatermarkingPage.vue)** | [`upstream/reference/WatermarkingPage.vue`](./upstream/reference/WatermarkingPage.vue) | • [`src/batch_filigrane/static/index.html`](./src/batch_filigrane/static/index.html)<br>• [`src/batch_filigrane/static/app.js`](./src/batch_filigrane/static/app.js) | • `WATERMARK_MAX_LENGTH = 100`<br>• `getWatermarkedFileName()` (`{nom}_filigrane.pdf`) |

