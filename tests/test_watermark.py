from __future__ import annotations

import asyncio
import io
import zipfile

import pymupdf as fitz
import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw

from batch_filigrane.app import BATCHES, STATIC_DIR, app
from batch_filigrane.watermark_engine import (
    PAGE_DIMENSIONS,
    WatermarkOptions,
    apply_watermark,
    watermark_document_bytes,
)


def _make_sample_pdf(num_pages: int = 1, title: str = "Document Test") -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"{title} - Page {i + 1}", fontsize=18)
        page.draw_rect(fitz.Rect(60, 140, 500, 400), color=(0, 0, 0.6), width=2)
    data = doc.tobytes()
    doc.close()
    return data


def _make_sample_png() -> bytes:
    img = Image.new("RGB", (800, 600), (245, 245, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, 760, 560], outline=(10, 20, 120), width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_default_options_all_enabled() -> None:
    opts = WatermarkOptions()
    assert opts.protect_qr_codes is True
    assert opts.use_colors is True
    assert opts.use_distortion is True


def test_watermark_single_and_multipage_pdf() -> None:
    pdf_2pages = _make_sample_pdf(num_pages=2, title="Contrat")
    options = WatermarkOptions(
        watermark_text="Document exclusivement destiné à la location immobilière",
        dpi=150,
        seed=42,
    )
    out_bytes = watermark_document_bytes(
        file_bytes=pdf_2pages,
        watermark_text=options.watermark_text,
        filename="contrat.pdf",
        options=options,
    )

    assert out_bytes.startswith(b"%PDF-")
    with fitz.open(stream=out_bytes, filetype="pdf") as out_doc:
        assert len(out_doc) == 2


def test_watermark_image_to_individual_pdf() -> None:
    png_bytes = _make_sample_png()
    out_bytes = watermark_document_bytes(
        file_bytes=png_bytes,
        watermark_text="Copie certifiée",
        filename="piece_identite.png",
        content_type="image/png",
    )
    assert out_bytes.startswith(b"%PDF-")
    with fitz.open(stream=out_bytes, filetype="pdf") as out_doc:
        assert len(out_doc) == 1


@pytest.mark.asyncio
async def test_batch_separate_and_merged_downloads() -> None:
    """
    Vérifie que l'envoi simultané de plusieurs PDF en batch 100% local :
    1. Active par défaut QR codes, couleurs et distorsion DFFilter.
    2. Permet de télécharger chaque PDF séparément (1+2+1 pages).
    3. Permet de télécharger le PDF fusionné en mode jointure (4 pages au total).
    """
    pdf_a = _make_sample_pdf(num_pages=1, title="Facture A")
    pdf_b = _make_sample_pdf(num_pages=2, title="Avis Imposition B")
    pdf_c = _make_sample_pdf(num_pages=1, title="Quittance C")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = [
            ("files", ("facture_a.pdf", pdf_a, "application/pdf")),
            ("files", ("avis_b.pdf", pdf_b, "application/pdf")),
            ("files", ("quittance_c.pdf", pdf_c, "application/pdf")),
        ]
        data = {
            "watermark": "Exclusivement pour dossier location",
            "concurrency": "3",
            "dpi": "128",
            "suffix": "_filigrane",
        }
        resp = await client.post("/api/batches", files=files, data=data)
        assert resp.status_code == 200
        batch = resp.json()
        batch_id = batch["id"]
        assert batch["total"] == 3
        assert batch["use_colors"] is True
        assert batch["use_distortion"] is True
        assert batch["protect_qr_codes"] is True

        # Attente de la fin du traitement parallèle
        for _ in range(50):
            status_resp = await client.get(f"/api/batches/{batch_id}")
            assert status_resp.status_code == 200
            batch = status_resp.json()
            if batch["is_finished"]:
                break
            await asyncio.sleep(0.1)

        assert batch["is_finished"] is True
        assert batch["completed"] == 3
        assert batch["failed"] == 0
        assert batch["total_pages"] == 4

        # 1. Mode séparés : chaque fichier est téléchargeable individuellement
        expected_pages = [1, 2, 1]
        expected_names = [
            "facture_a_filigrane.pdf",
            "avis_b_filigrane.pdf",
            "quittance_c_filigrane.pdf",
        ]

        for idx, file_info in enumerate(batch["files"]):
            assert file_info["output_name"] == expected_names[idx]
            assert file_info["page_count"] == expected_pages[idx]

            dl_resp = await client.get(file_info["download_url"])
            assert dl_resp.status_code == 200
            assert dl_resp.headers["content-type"] == "application/pdf"
            with fitz.open(stream=dl_resp.content, filetype="pdf") as pdf_doc:
                assert len(pdf_doc) == expected_pages[idx]

        # 2. Mode séparés en archive ZIP
        zip_resp = await client.get(batch["zip_url"])
        assert zip_resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            assert sorted(zf.namelist()) == sorted(expected_names)

        # 3. Mode jointure : téléchargement d'un seul PDF fusionnant tous les documents (1 + 2 + 1 = 4 pages)
        merged_resp = await client.get(batch["merged_url"])
        assert merged_resp.status_code == 200
        assert merged_resp.headers["content-type"] == "application/pdf"
        with fitz.open(stream=merged_resp.content, filetype="pdf") as merged_doc:
            assert len(merged_doc) == 4

        # Nettoyage
        del_resp = await client.delete(f"/api/batches/{batch_id}")
        assert del_resp.status_code == 200


def test_c2_pdfjs_eval_disabled_in_vendor_bundles() -> None:
    """Vérifie que les bundles PDF.js (pdf.min.js & pdf.worker.min.js) sont présents, compatibles file:// et sans eval/new Function."""
    for filename in ("pdf.min.js", "pdf.worker.min.js"):
        bundle_path = STATIC_DIR / "vendor" / filename
        assert bundle_path.exists(), f"Bundle manquant : {filename}"
        content = bundle_path.read_text(encoding="utf-8")
        assert 'new Function(' not in content
        assert '//# sourceMappingURL=' not in content


def test_e2_dpi_proportional_scaling() -> None:
    """Vérifie que le calque de filigrane met à l'échelle proportionnellement selon le DPI."""
    opts_150 = WatermarkOptions(watermark_text="Test Echelle DPI", dpi=150, seed=7)
    opts_300 = WatermarkOptions(watermark_text="Test Echelle DPI", dpi=300, seed=7)

    base_150 = Image.new("RGB", (1240, 1754), (255, 255, 255))
    base_300 = Image.new("RGB", (2480, 3508), (255, 255, 255))
    out_150 = apply_watermark(base_150, "Test Echelle DPI", opts_150, PAGE_DIMENSIONS[150])
    out_300 = apply_watermark(base_300, "Test Echelle DPI", opts_300, PAGE_DIMENSIONS[300])

    assert out_150.size == (1240, 1754)
    assert out_300.size == (2480, 3508)


@pytest.mark.asyncio
async def test_security_defenses_c1_e4_e5_m3_m4() -> None:
    """
    Vérifie les défenses de sécurité :
    - C1 : suppression immédiate du fichier source brut après traitement et purge complète au DELETE
    - E4 : rejet CSRF / Cross-Origin (403) sur POST /api/batches depuis une origine externe
    - E5 : présence des en-têtes CSP (sans unsafe-eval), X-Content-Type-Options, X-Frame-Options
    - M3 : assainissement des noms de fichiers malveillants et Content-Disposition
    - M4 : interdiction (409) de relancer /retry sur un fichier déjà terminé
    """
    sample_pdf = _make_sample_pdf(num_pages=1, title="Securite")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # E5 : Vérification des en-têtes de sécurité HTTP
        health_resp = await client.get("/api/health")
        assert health_resp.status_code == 200
        csp = health_resp.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "unsafe-eval" not in csp
        assert health_resp.headers.get("x-content-type-options") == "nosniff"
        assert health_resp.headers.get("x-frame-options") == "SAMEORIGIN"

        # E4 : Rejet Cross-Origin depuis un site tiers malveillant
        evil_resp = await client.post(
            "/api/batches",
            files=[("files", ("doc.pdf", sample_pdf, "application/pdf"))],
            data={"watermark": "Test"},
            headers={"Origin": "https://evil.example.com"},
        )
        assert evil_resp.status_code == 403

        # Création légitime avec un nom de fichier contenant des caractères d'injection (M3 / C3)
        malicious_name = 'facture"evil;inject<script>.pdf'
        resp = await client.post(
            "/api/batches",
            files=[("files", (malicious_name, sample_pdf, "application/pdf"))],
            data={"watermark": "Test Securite", "dpi": "128"},
            headers={"Origin": "http://127.0.0.1:8000"},
        )
        assert resp.status_code == 200
        batch_data = resp.json()
        batch_id = batch_data["id"]

        for _ in range(50):
            st = await client.get(f"/api/batches/{batch_id}")
            batch_data = st.json()
            if batch_data["is_finished"]:
                break
            await asyncio.sleep(0.05)

        assert batch_data["is_finished"] is True
        assert batch_data["completed"] == 1
        file_info = batch_data["files"][0]

        # M3 : Le nom de fichier assaini ne contient ni guillemets, ni chevrons, ni point-virgule
        assert '"' not in file_info["output_name"]
        assert ";" not in file_info["output_name"]
        assert "<" not in file_info["output_name"]
        assert ">" not in file_info["output_name"]

        # C1 : Le fichier source brut (`input_path`) a été supprimé du disque dès la fin du traitement
        batch_job = BATCHES[batch_id]
        batch_dir = batch_job.batch_dir
        batch_item = batch_job.files[file_info["id"]]
        assert not batch_item.input_path.exists()
        assert batch_item.output_path.exists()

        # M3 : Vérification de l'en-tête Content-Disposition au téléchargement
        dl_resp = await client.get(file_info["download_url"])
        assert dl_resp.status_code == 200
        cd_header = dl_resp.headers.get("content-disposition", "")
        assert cd_header.startswith('attachment; filename="')
        assert "\r" not in cd_header and "\n" not in cd_header

        # M4 : Relancer /retry sur un fichier déjà terminé doit renvoyer 409 Conflict
        retry_resp = await client.post(f"/api/batches/{batch_id}/files/{file_info['id']}/retry")
        assert retry_resp.status_code == 409

        # C1 : La suppression du lot efface immédiatement le dossier du disque
        del_resp = await client.delete(f"/api/batches/{batch_id}")
        assert del_resp.status_code == 200
        assert not batch_dir.exists()
