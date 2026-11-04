"""
Portage Python fidèle du moteur officiel de filigrane de FiligraneFacile (beta.gouv.fr).

Basé directement sur le code source open-source officiel du Ministère (MTES-MCT) :
- Front-end : https://github.com/MTES-MCT/Dossier-Facile-Frontend/tree/main/watermark
- Back-end  : https://github.com/MTES-MCT/dossierfacile-backend (module dossierfacile-pdf-generator)
  - BOPdfDocumentTemplate.java
  - DFFilter.java
  - PdfTemplateParameters.java
  - PageDimension.java
"""

from __future__ import annotations

import io
import logging
import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

logger = logging.getLogger(__name__)

# Protection contre les bombes de décompression d'images (DecompressionBombError)
Image.MAX_IMAGE_PIXELS = 80_000_000
MAX_PDF_PAGES = 200

# Constantes officielles issues de BOPdfDocumentTemplate.java & PdfTemplateParameters.java
DEFAULT_WATERMARK_URL = " https://filigrane.beta.gouv.fr/   "
DEFAULT_WATERMARK_TEXT = "Document exclusivement destiné à la location immobilière"

# Dimensions A4 en points PDF (PDRectangle.A4 dans Apache PDFBox)
A4_WIDTH_PT = 595.27563
A4_HEIGHT_PT = 841.8898
RATIO_PDF = A4_HEIGHT_PT / A4_WIDTH_PT

# Largeur de référence A4150 utilisée dans BOPdfDocumentTemplate.java pour le calibrage visuel
REFERENCE_A4150_WIDTH = 1240.0

# Couleurs officielles de BOPdfDocumentTemplate.COLORS (RGBA)
OFFICIAL_COLORS: tuple[tuple[int, int, int, int], ...] = (
    (64, 64, 64, 255),
    (32, 32, 32, 220),
    (0, 0, 0, 110),
    (0, 0, 91, 170),
    (255, 0, 0, 170),
)
DARK_GRAY_RGBA: tuple[int, int, int, int] = (64, 64, 64, 255)


@dataclass(frozen=True)
class PageDimension:
    """Équivalent de fr.dossierfacile.api.pdfgenerator.model.PageDimension."""

    width: int
    height: int
    dpi: int


PAGE_DIMENSIONS: dict[int, PageDimension] = {
    128: PageDimension(1058, 1496, 128),  # PageDimension.A4128
    150: PageDimension(1240, 1754, 150),  # PageDimension.A4150 (Défaut officiel)
    220: PageDimension(1819, 2573, 220),  # Haute définition
    300: PageDimension(2480, 3508, 300),  # Très haute définition
}


@dataclass
class WatermarkOptions:
    """Options de configuration du moteur de filigrane local."""

    watermark_text: str = DEFAULT_WATERMARK_TEXT
    dpi: int = 150
    compression_quality: int = 90  # 0.9f dans PdfTemplateParameters.java
    use_colors: bool = True        # FeatureFlipping.shouldUseColors() (activé par défaut)
    use_distortion: bool = True    # FeatureFlipping.shouldUseDistortion() (DFFilter, activé par défaut)
    protect_qr_codes: bool = True  # Détection et préservation des QR codes (detectQRCodes, activé par défaut)
    seed: int | None = None        # Pour tests déterministes si souhaité


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Charge la police Arial (comme `new Font("Arial", Font.PLAIN, ...)` dans BOPdfDocumentTemplate.java)
    ou une police sans-serif équivalente selon l'OS.
    """
    font_candidates = [
        "arial.ttf",
        "Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for candidate in font_candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _get_scale(page_width: float, page_height: float, max_page_width: int) -> float:
    """
    Équivalent exact de `BOPdfDocumentTemplate.getScale(PDRectangle pageMediaBox)`.
    """
    ratio_image = page_height / float(page_width)
    dim_width = page_width if (ratio_image < RATIO_PDF) else (page_height / RATIO_PDF)
    return 1.0 if (dim_width < max_page_width) else (max_page_width / float(page_width))


def iter_converted_images(
    file_bytes: bytes,
    filename: str = "",
    content_type: str = "",
    max_page: PageDimension = PAGE_DIMENSIONS[150],
) -> Iterator[Image.Image]:
    """
    Générateur traitant les pages une par une en flux pour éviter toute saturation mémoire
    sur les PDF volumineux (équivalent streaming de `BOPdfDocumentTemplate.convertToImages`).
    """
    is_pdf = (
        content_type.lower() == "application/pdf"
        or filename.lower().endswith(".pdf")
        or file_bytes[:5] == b"%PDF-"
    )

    if is_pdf:
        with fitz.open(stream=file_bytes, filetype="pdf") as pdf_doc:
            num_pages = len(pdf_doc)
            if num_pages == 0:
                raise ValueError("Le document PDF ne contient aucune page.")
            if num_pages > MAX_PDF_PAGES:
                raise ValueError(
                    f"Le document PDF dépasse la limite de sécurité de {MAX_PDF_PAGES} pages ({num_pages} pages)."
                )
            for page_index in range(num_pages):
                page = pdf_doc[page_index]
                rect = page.rect
                scale = _get_scale(rect.width, rect.height, max_page.width)
                # x2 - double la résolution d'image comme dans BOPdfDocumentTemplate.java (ligne 153)
                render_scale = max(0.5, scale * 2.0)
                matrix = fitz.Matrix(render_scale, render_scale)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                del pix
                yield img
        return

    # Traitement d'une image (JPG, PNG, WebP, GIF, etc.) avec gestion de l'orientation EXIF
    with Image.open(io.BytesIO(file_bytes)) as raw_img:
        transposed = ImageOps.exif_transpose(raw_img)
        if transposed.mode in ("RGBA", "LA") or (
            transposed.mode == "P" and "transparency" in transposed.info
        ):
            rgba = transposed.convert("RGBA")
            white_bg = Image.new("RGB", rgba.size, (255, 255, 255))
            white_bg.paste(rgba, mask=rgba.split()[3])
            yield white_bg
            return
        yield transposed.convert("RGB")


def convert_to_images(
    file_bytes: bytes,
    filename: str = "",
    content_type: str = "",
    max_page: PageDimension = PAGE_DIMENSIONS[150],
) -> list[Image.Image]:
    """Retourne la liste des images converties (utile pour les tests unitaires)."""
    return list(
        iter_converted_images(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            max_page=max_page,
        )
    )


def fit_image_to_page(
    bim: Image.Image,
    max_page: PageDimension = PAGE_DIMENSIONS[150],
) -> Image.Image:
    """
    Équivalent exact de `BOPdfDocumentTemplate.fitImageToPage(BufferedImage bim)`.
    Ajuste et centre l'image aux proportions d'une page A4 sur fond blanc.
    """
    w, h = bim.size
    ratio_image = h / float(w)

    if ratio_image < RATIO_PDF:
        dim_w = w
        dim_h = int(w * RATIO_PDF)
    else:
        dim_w = int(h / RATIO_PDF)
        dim_h = h

    # Si l'image dépasse la largeur maximale A4, mise à l'échelle
    scale = 1.0 if (dim_w < max_page.width) else (max_page.width / float(dim_w))

    result_w = max(1, int(scale * dim_w))
    result_h = max(1, int(scale * dim_h))

    scaled_w = max(1, int(round(scale * w)))
    scaled_h = max(1, int(round(scale * h)))

    offset_x = int(round(scale * (dim_w - w) / 2.0))
    offset_y = int(round(scale * (dim_h - h) / 2.0))

    result_image = Image.new("RGB", (result_w, result_h), (255, 255, 255))
    if (scaled_w, scaled_h) != (w, h):
        resized = bim.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
    else:
        resized = bim

    result_image.paste(resized, (offset_x, offset_y))
    return result_image


def apply_df_distortion_filter(
    src_image: Image.Image,
    x_frequency: int = 12,
    y_frequency: int = 8,
    max_distortion: int = 28,
) -> Image.Image:
    """
    Portage vectorisé NumPy de `DFFilter.java` :
        r = sin(x * xFrequency / width)
        out_y = y + maxDistorsion * sin(y * yFrequency / height) * r * r
    """
    arr = np.array(src_image)
    height, width = arr.shape[:2]

    x_coords = np.arange(width, dtype=np.float32)
    y_coords = np.arange(height, dtype=np.float32)

    r = np.sin(x_coords * float(x_frequency) / float(width))  # (width,)
    r_sq = r * r  # (width,)

    y_sin = float(max_distortion) * np.sin(y_coords * float(y_frequency) / float(height))  # (height,)

    # Matrice d'indices Y source : shape (height, width)
    src_y = y_coords[:, None] + y_sin[:, None] * r_sq[None, :]
    src_y_idx = np.clip(np.rint(src_y).astype(np.int32), 0, height - 1)
    src_x_idx = np.arange(width, dtype=np.int32)[None, :]

    distorted = arr[src_y_idx, src_x_idx]
    return Image.fromarray(distorted, mode="RGBA")


def detect_qr_codes(bim: Image.Image) -> list[tuple[int, int, int, int]]:
    """
    Équivalent de `BOPdfDocumentTemplate.detectQRCodes(BufferedImage image)`.
    Retourne une liste de boîtes englobantes (x, y, w, h) pour chaque QR code détecté.
    """
    try:
        rgb = np.array(bim)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        detector = cv2.QRCodeDetector()
        retval, points = detector.detectMulti(gray)
        boxes: list[tuple[int, int, int, int]] = []
        if retval and points is not None:
            for quad in points:
                xs = quad[:, 0]
                ys = quad[:, 1]
                x_min = max(0, int(np.floor(np.min(xs))))
                y_min = max(0, int(np.floor(np.min(ys))))
                x_max = int(np.ceil(np.max(xs)))
                y_max = int(np.ceil(np.max(ys)))
                if x_max > x_min and y_max > y_min:
                    boxes.append((x_min, y_min, x_max - x_min, y_max - y_min))
        return boxes
    except Exception as exc:
        logger.debug("Détection QR code ignorée: %s", exc)
        return []


def apply_watermark(
    bim: Image.Image,
    watermark_text: str,
    options: WatermarkOptions | None = None,
    max_page: PageDimension = PAGE_DIMENSIONS[150],
) -> Image.Image:
    """
    Équivalent fidèle de `BOPdfDocumentTemplate.applyWatermark(BufferedImage bim, String watermarkText)`.

    Les constantes visuelles (taille de police 28px, flou gaussien 45..65px, distorsion 28px,
    marge QR code 20px) sont calibrées par rapport à la largeur de référence A4150 (1240px)
    afin que le rendu soit visuellement identique quel que soit le DPI choisi (128, 150, 220, 300).
    """
    opts = options or WatermarkOptions(watermark_text=watermark_text)
    rng = random.Random(opts.seed)

    # Préparation du texte (lignes 109-111 de BOPdfDocumentTemplate.java)
    cleaned_text = (watermark_text or "").strip()
    watermark_to_apply = f"{cleaned_text}   " if cleaned_text else DEFAULT_WATERMARK_URL

    w, h = bim.size
    dpi_scale = w / REFERENCE_A4150_WIDTH
    diagonal = int(math.sqrt(w * w + h * h))

    # 1. Création du calque de filigrane (diagonal x diagonal)
    watermark_layer = Image.new("RGBA", (diagonal, diagonal), (0, 0, 0, 0))
    draw = ImageDraw.Draw(watermark_layer)

    repeats = 1 + (128 // max(1, len(watermark_to_apply)))
    repeated_watermark = watermark_to_apply * repeats

    layer_opacity = rng.uniform(0.52, 0.60)
    space_between_text = diagonal / rng.uniform(8.0, 10.0)

    font_size = max(10, int(round(28 * dpi_scale)))
    font = _load_font(font_size)

    for i in range(1, 11):
        if opts.use_colors:
            base_r, base_g, base_b, base_a = OFFICIAL_COLORS[rng.randint(0, len(OFFICIAL_COLORS) - 1)]
        else:
            base_r, base_g, base_b, base_a = DARK_GRAY_RGBA

        effective_alpha = max(0, min(255, int(round(base_a * layer_opacity))))
        y_pos = i * space_between_text
        # Dessin avec ligne de base à y_pos comme Graphics2D.drawString(watermark, 0, i * spaceBetweenText)
        draw.text(
            (0, y_pos - font_size),
            repeated_watermark,
            font=font,
            fill=(base_r, base_g, base_b, effective_alpha),
        )

    # 2. Création du calque flou gaussien proportionnel au DPI (lignes 262-271 de BOPdfDocumentTemplate.java)
    radius = rng.randint(45, 65) * dpi_scale
    sigma = radius / 3.0  # Dans getGaussianBlurFilter: float sigma = radius / 3.0f
    blur_opacity = rng.uniform(0.75, 0.95)

    # Mise à l'échelle de la couche alpha avant le flou gaussien
    r_ch, g_ch, b_ch, a_ch = watermark_layer.split()
    scaled_a = a_ch.point(lambda p: int(round(p * blur_opacity)))
    blurred_text_layer = Image.merge("RGBA", (r_ch, g_ch, b_ch, scaled_a))
    blurred_text_layer = blurred_text_layer.filter(ImageFilter.GaussianBlur(radius=sigma))

    # 3. Distorsion optionnelle proportionnelle au DPI (DFFilter.java)
    if opts.use_distortion:
        scaled_distortion = max(1, int(round(28 * dpi_scale)))
        buffer_layer = apply_df_distortion_filter(
            watermark_layer,
            max_distortion=scaled_distortion,
        )
    else:
        buffer_layer = watermark_layer

    # 4. Superposition buffer + blurredTextLayer puis rotation de -25° (AWT) = +25° (Pillow)
    combined_watermark = Image.alpha_composite(buffer_layer, blurred_text_layer)
    rotated = combined_watermark.rotate(
        25,
        resample=Image.Resampling.BILINEAR,
        expand=False,
        center=(diagonal / 2.0, diagonal / 2.0),
    )

    # 5. Recadrage centré aux dimensions de la page (bim.width x bim.height)
    crop_left = diagonal // 2 - w // 2
    crop_top = diagonal // 2 - h // 2
    cropped_rotated = rotated.crop((crop_left, crop_top, crop_left + w, crop_top + h))

    # 6. Détection et préservation des QR codes (lignes 294-307 de BOPdfDocumentTemplate.java)
    if opts.protect_qr_codes:
        qr_boxes = detect_qr_codes(bim)
        if qr_boxes:
            logger.info("[QR_CODE] QR codes détectés : %d", len(qr_boxes))
            magic_border_size = max(4, int(round(20 * dpi_scale)))
            qr_draw = ImageDraw.Draw(cropped_rotated)
            for qx, qy, qw, qh in qr_boxes:
                x0 = max(qx - magic_border_size, 0)
                y0 = max(qy - magic_border_size, 0)
                x1 = min(qx + qw + magic_border_size, w)
                y1 = min(qy + qh + magic_border_size, h)
                qr_draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 0))

    # 7. Fusion finale avec l'image de la page
    final_rgba = Image.alpha_composite(bim.convert("RGBA"), cropped_rotated)
    return final_rgba.convert("RGB")


def _append_image_page_to_pdf(
    out_pdf: fitz.Document,
    bim: Image.Image,
    dpi: int = 150,
    compression_quality: int = 90,
) -> None:
    """Encode une image en JPEG et l'ajoute comme page A4 au document PDF fourni."""
    page = out_pdf.new_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
    img_buffer = io.BytesIO()
    bim.save(
        img_buffer,
        format="JPEG",
        quality=compression_quality,
        dpi=(dpi, dpi),
        optimize=True,
    )
    jpeg_bytes = img_buffer.getvalue()
    drawn_height = bim.height * A4_WIDTH_PT / float(bim.width)
    target_rect = fitz.Rect(0, 0, A4_WIDTH_PT, drawn_height)
    page.insert_image(target_rect, stream=jpeg_bytes)


def images_to_a4_pdf(
    images: Sequence[Image.Image],
    dpi: int = 150,
    compression_quality: int = 90,
) -> bytes:
    """
    Équivalent de `BOPdfDocumentTemplate.addImageAsPageToDocument(PDDocument, BufferedImage)`.
    Encode chaque image en JPEG (qualité 90%, DPI configuré) et l'insère dans une page PDF A4.
    """
    out_pdf = fitz.open()
    try:
        for bim in images:
            _append_image_page_to_pdf(
                out_pdf=out_pdf,
                bim=bim,
                dpi=dpi,
                compression_quality=compression_quality,
            )
        return out_pdf.tobytes(garbage=4, deflate=True)
    finally:
        out_pdf.close()


def watermark_document_bytes(
    file_bytes: bytes,
    watermark_text: str,
    filename: str = "document.pdf",
    content_type: str = "application/pdf",
    options: WatermarkOptions | None = None,
) -> bytes:
    """
    Traite un document unique (PDF ou image) page par page en flux avec l'algorithme
    de FiligraneFacile et retourne les octets du PDF filigrané individuel.
    """
    opts = options or WatermarkOptions(watermark_text=watermark_text)
    max_page = PAGE_DIMENSIONS.get(opts.dpi, PAGE_DIMENSIONS[150])

    out_pdf = fitz.open()
    try:
        for raw_img in iter_converted_images(
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            max_page=max_page,
        ):
            fitted = fit_image_to_page(raw_img, max_page=max_page)
            raw_img.close()
            watermarked = apply_watermark(
                bim=fitted,
                watermark_text=watermark_text,
                options=opts,
                max_page=max_page,
            )
            fitted.close()
            _append_image_page_to_pdf(
                out_pdf=out_pdf,
                bim=watermarked,
                dpi=max_page.dpi,
                compression_quality=opts.compression_quality,
            )
            watermarked.close()

        return out_pdf.tobytes(garbage=4, deflate=True)
    finally:
        out_pdf.close()


def merge_pdfs(pdf_sources: Sequence[Path | bytes]) -> bytes:
    """
    Fusionne plusieurs fichiers PDF (chemins ou octets) dans l'ordre en un seul PDF unique.
    """
    merged_doc = fitz.open()
    try:
        for src in pdf_sources:
            if isinstance(src, Path):
                with fitz.open(str(src)) as part_doc:
                    merged_doc.insert_pdf(part_doc)
            else:
                with fitz.open(stream=src, filetype="pdf") as part_doc:
                    merged_doc.insert_pdf(part_doc)
        return merged_doc.tobytes(garbage=4, deflate=True)
    finally:
        merged_doc.close()
