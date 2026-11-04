"""
Serveur FastAPI 100% local pour le traitement par lot (batch) en parallèle de documents PDF et images
avec le moteur open-source de FiligraneFacile (beta.gouv.fr).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import re
import shutil
import tempfile
import time
import urllib.parse
import uuid
import zipfile
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pymupdf as fitz
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .watermark_engine import (
    DEFAULT_WATERMARK_TEXT,
    WatermarkOptions,
    merge_pdfs,
    watermark_document_bytes,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
STORAGE_ROOT = Path(tempfile.gettempdir()) / "batch_filigrane_sessions"
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

# Limites de sécurité et durée de rétention maximale en mémoire/disque
MAX_FILES_PER_BATCH = 500
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 Mo par fichier
UPLOAD_CHUNK_SIZE = 64 * 1024           # 64 Ko par bloc de lecture
BATCH_TTL_SECONDS = 1800.0              # Purge automatique après 30 minutes
CLEANUP_INTERVAL_SECONDS = 120.0

ALLOWED_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "test", "testserver"}
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "connect-src 'self' blob:; "
    "frame-src 'self' blob:; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'"
)

FileStatus = Literal["queued", "processing", "completed", "error"]


@dataclass
class BatchFileItem:
    id: str
    index: int
    original_name: str
    output_name: str
    content_type: str
    watermark_text: str
    input_path: Path
    output_path: Path
    input_size: int
    output_size: int = 0
    page_count: int = 0
    status: FileStatus = "queued"
    error_message: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.perf_counter()
        return int((end - self.started_at) * 1000)

    def to_dict(self, batch_id: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "original_name": self.original_name,
            "output_name": self.output_name,
            "content_type": self.content_type,
            "watermark_text": self.watermark_text,
            "input_size": self.input_size,
            "output_size": self.output_size,
            "page_count": self.page_count,
            "status": self.status,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
            "download_url": f"/api/batches/{batch_id}/files/{self.id}/download",
            "preview_url": f"/api/batches/{batch_id}/files/{self.id}/preview",
        }


@dataclass
class BatchJob:
    id: str
    created_at: float
    watermark_text: str
    concurrency: int
    options: WatermarkOptions
    suffix: str
    batch_dir: Path
    files: dict[str, BatchFileItem] = field(default_factory=dict)
    file_order: list[str] = field(default_factory=list)
    semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.semaphore = asyncio.Semaphore(max(1, self.concurrency))

    def get_merged_filename(self) -> str:
        ordered_items = [self.files[fid] for fid in self.file_order if fid in self.files]
        stems = [Path(item.original_name).stem or f"doc{i+1}" for i, item in enumerate(ordered_items)]
        clean_suffix = self.suffix if self.suffix is not None else "_filigrane"
        if 0 < len(stems) <= 3:
            joined = "_".join(stems)
            return f"{joined}{clean_suffix}.pdf"
        return f"documents_fusionnes_{len(stems)}{clean_suffix}.pdf"

    def to_dict(self) -> dict[str, Any]:
        ordered_items = [self.files[fid] for fid in self.file_order if fid in self.files]
        total = len(ordered_items)
        completed = sum(1 for item in ordered_items if item.status == "completed")
        failed = sum(1 for item in ordered_items if item.status == "error")
        active = sum(1 for item in ordered_items if item.status == "processing")
        total_pages = sum(item.page_count for item in ordered_items if item.status == "completed")
        done_count = completed + failed
        percent = int(round((done_count / total) * 100)) if total > 0 else 100
        is_finished = done_count == total

        return {
            "id": self.id,
            "watermark_text": self.watermark_text,
            "concurrency": self.concurrency,
            "dpi": self.options.dpi,
            "use_colors": self.options.use_colors,
            "use_distortion": self.options.use_distortion,
            "protect_qr_codes": self.options.protect_qr_codes,
            "suffix": self.suffix,
            "total": total,
            "completed": completed,
            "failed": failed,
            "active": active,
            "total_pages": total_pages,
            "percent": percent,
            "is_finished": is_finished,
            "zip_url": f"/api/batches/{self.id}/zip",
            "merged_url": f"/api/batches/{self.id}/merged",
            "merged_preview_url": f"/api/batches/{self.id}/merged/preview",
            "merged_filename": self.get_merged_filename(),
            "files": [item.to_dict(self.id) for item in ordered_items],
        }


BATCHES: dict[str, BatchJob] = {}
BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _spawn_background_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """
    Crée une tâche asyncio en conservant une référence forte dans BACKGROUND_TASKS
    pour éviter sa suppression prématurée par le ramasse-miettes Python.
    """
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


def _sanitize_filename(name: str, fallback: str = "document.pdf") -> str:
    """Nettoie un nom de fichier de tout caractère de contrôle, chemin, séparateur ou caractère spécial."""
    unquoted = urllib.parse.unquote(name or fallback)
    base = Path(unquoted).name
    cleaned = re.sub(r'[\x00-\x1f\x7f/\\<>:"|?*%;]+', "_", base).strip(" ._")
    return cleaned or fallback


def build_output_filename(original_name: str, suffix: str = "_filigrane") -> str:
    """
    Construit le nom du fichier PDF sortant sur le modèle de `getWatermarkedFileName()`
    dans `WatermarkingPage.vue` (`{nom_sans_extension}_filigrane.pdf`).
    """
    safe_orig = _sanitize_filename(original_name)
    stem = Path(safe_orig).stem or "document"
    clean_suffix = re.sub(r'[\x00-\x1f\x7f/\\<>:"|?*%;]+', "", suffix if suffix is not None else "_filigrane")
    return f"{stem}{clean_suffix}.pdf"


def _count_pdf_pages(pdf_bytes: bytes) -> int:
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return len(doc)
    except Exception:
        return 1


async def _process_batch_item(batch: BatchJob, item: BatchFileItem) -> None:
    """Traite un fichier individuel en parallèle en local sous le contrôle du sémaphore du lot."""
    async with batch.semaphore:
        item.started_at = time.perf_counter()
        item.finished_at = None
        item.error_message = None
        item.status = "processing"

        try:
            file_bytes = await asyncio.to_thread(item.input_path.read_bytes)

            item_options = WatermarkOptions(
                watermark_text=item.watermark_text,
                dpi=batch.options.dpi,
                compression_quality=batch.options.compression_quality,
                use_colors=batch.options.use_colors,
                use_distortion=batch.options.use_distortion,
                protect_qr_codes=batch.options.protect_qr_codes,
            )
            pdf_bytes = await asyncio.to_thread(
                watermark_document_bytes,
                file_bytes,
                item.watermark_text,
                item.original_name,
                item.content_type,
                item_options,
            )

            await asyncio.to_thread(item.output_path.write_bytes, pdf_bytes)
            item.output_size = len(pdf_bytes)
            item.page_count = await asyncio.to_thread(_count_pdf_pages, pdf_bytes)
            item.status = "completed"

            # Suppression immédiate du fichier original non filigrané du disque (C1)
            await asyncio.to_thread(item.input_path.unlink, True)
        except Exception as exc:
            logger.exception("Erreur lors du traitement de %s", item.original_name)
            item.status = "error"
            item.error_message = str(exc)
        finally:
            item.finished_at = time.perf_counter()


def _purge_storage_root_sync() -> None:
    """Supprime tous les dossiers de session temporaires présents dans STORAGE_ROOT."""
    if not STORAGE_ROOT.exists():
        return
    for child in STORAGE_ROOT.iterdir():
        with contextlib.suppress(Exception):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


async def _cleanup_expired_batches_loop() -> None:
    """Tâche périodique supprimant automatiquement les lots expirés (TTL)."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
        now = time.time()
        expired_ids = [
            bid
            for bid, batch in list(BATCHES.items())
            if (now - batch.created_at) > BATCH_TTL_SECONDS
        ]
        for bid in expired_ids:
            batch = BATCHES.pop(bid, None)
            if batch and batch.batch_dir.exists():
                await asyncio.to_thread(shutil.rmtree, batch.batch_dir, True)
                logger.info("Lot expiré purgé automatiquement : %s", bid)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Nettoyage au démarrage de tout fichier résiduel d'une session précédente
    await asyncio.to_thread(_purge_storage_root_sync)
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    cleanup_task = _spawn_background_task(_cleanup_expired_batches_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        BATCHES.clear()
        await asyncio.to_thread(_purge_storage_root_sync)


app = FastAPI(
    title="Batch-Filigrane (100% Local)",
    description=(
        "Traitement par lot en parallèle 100% local de fichiers PDF et images avec le moteur "
        "open-source de FiligraneFacile (beta.gouv.fr) — Téléchargement séparé ou fusionné."
    ),
    version="1.2.0",
    lifespan=_lifespan,
)

# Protection contre le DNS Rebinding (E4)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "::1", "test", "testserver"],
)


@app.middleware("http")
async def local_security_middleware(request: Request, call_next: Any) -> Response:
    """
    1. Protège les routes mutantes (/api/...) contre les requêtes Cross-Site (CSRF depuis un site tiers).
    2. Ajoute les en-têtes de sécurité (CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy).
    """
    if request.method in {"POST", "PUT", "DELETE", "PATCH"} and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            parsed = urllib.parse.urlparse(origin)
            hostname = (parsed.hostname or "").lower()
            if hostname not in ALLOWED_LOCAL_HOSTS:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Requête cross-origin non autorisée."},
                )

    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", CSP_POLICY)
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "active_batches": len(BATCHES)}


async def _stream_upload_to_path(upload: UploadFile, target_path: Path, max_bytes: int) -> int:
    """
    Copie un fichier téléversé par blocs de 64 Ko sur le disque en vérifiant la taille maximale
    sans charger l'intégralité du fichier en RAM (M2).
    """
    total_written = 0
    with target_path.open("wb") as out_f:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total_written += len(chunk)
            if total_written > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Le fichier '{upload.filename}' dépasse la limite autorisée ({max_bytes // (1024 * 1024)} Mo).",
                )
            await asyncio.to_thread(out_f.write, chunk)
    return total_written


@app.post("/api/batches")
async def create_batch(
    files: list[UploadFile] = File(...),
    watermark: str = Form(DEFAULT_WATERMARK_TEXT),
    concurrency: int = Form(6),
    dpi: int = Form(150),
    use_colors: bool = Form(True),
    use_distortion: bool = Form(True),
    protect_qr_codes: bool = Form(True),
    suffix: str = Form("_filigrane"),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="Aucun fichier fourni.")
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Nombre maximal de fichiers par lot dépassé ({MAX_FILES_PER_BATCH} max).",
        )

    batch_id = uuid.uuid4().hex[:12]
    batch_dir = STORAGE_ROOT / batch_id
    input_dir = batch_dir / "input"
    output_dir = batch_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    clamped_concurrency = max(1, min(16, int(concurrency)))
    valid_dpi = dpi if dpi in (128, 150, 220, 300) else 150

    options = WatermarkOptions(
        watermark_text=watermark,
        dpi=valid_dpi,
        use_colors=use_colors,
        use_distortion=use_distortion,
        protect_qr_codes=protect_qr_codes,
    )

    batch = BatchJob(
        id=batch_id,
        created_at=time.time(),
        watermark_text=watermark,
        concurrency=clamped_concurrency,
        options=options,
        suffix=suffix,
        batch_dir=batch_dir,
    )

    used_output_names: dict[str, int] = {}

    try:
        for idx, upload in enumerate(files):
            file_id = f"{idx:04d}_{uuid.uuid4().hex[:8]}"
            orig_name = _sanitize_filename(upload.filename or f"document_{idx + 1}.pdf")
            base_output_name = build_output_filename(orig_name, suffix=suffix)

            if base_output_name.lower() in used_output_names:
                used_output_names[base_output_name.lower()] += 1
                count = used_output_names[base_output_name.lower()]
                out_stem = Path(base_output_name).stem
                final_output_name = f"{out_stem}_{count}.pdf"
            else:
                used_output_names[base_output_name.lower()] = 1
                final_output_name = base_output_name

            ext = Path(orig_name).suffix.lower() or ".bin"
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
                ext = ".bin"
            input_path = input_dir / f"{file_id}{ext}"
            output_path = output_dir / f"{file_id}.pdf"

            written_size = await _stream_upload_to_path(
                upload=upload,
                target_path=input_path,
                max_bytes=MAX_FILE_SIZE_BYTES,
            )

            item = BatchFileItem(
                id=file_id,
                index=idx,
                original_name=orig_name,
                output_name=final_output_name,
                content_type=upload.content_type or "application/pdf",
                watermark_text=watermark,
                input_path=input_path,
                output_path=output_path,
                input_size=written_size,
            )
            batch.files[file_id] = item
            batch.file_order.append(file_id)
    except Exception:
        await asyncio.to_thread(shutil.rmtree, batch_dir, True)
        raise

    BATCHES[batch_id] = batch

    # Lancement de tous les traitements individuels en parallèle avec références fortes (M1)
    for fid in batch.file_order:
        _spawn_background_task(_process_batch_item(batch, batch.files[fid]))

    return batch.to_dict()


@app.get("/api/batches/{batch_id}")
async def get_batch_status(batch_id: str) -> dict[str, Any]:
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Lot introuvable.")
    return batch.to_dict()


@app.post("/api/batches/{batch_id}/files/{file_id}/retry")
async def retry_file(batch_id: str, file_id: str) -> dict[str, Any]:
    batch = BATCHES.get(batch_id)
    if not batch or file_id not in batch.files:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")

    item = batch.files[file_id]
    if item.status in ("processing", "queued"):
        raise HTTPException(status_code=409, detail="Ce fichier est déjà en cours de traitement.")
    if item.status == "completed":
        raise HTTPException(status_code=409, detail="Ce fichier a déjà été traité avec succès.")
    if not item.input_path.exists():
        raise HTTPException(status_code=410, detail="Le fichier source temporaire n'est plus disponible.")

    item.status = "queued"
    item.error_message = None
    _spawn_background_task(_process_batch_item(batch, item))
    return item.to_dict(batch_id)


def _content_disposition_header(filename: str, disposition_type: str = "attachment") -> str:
    """
    Construit un en-tête Content-Disposition sécurisé RFC 6266, protégé contre l'injection
    de guillemets, points-virgules, antislashs ou retours chariot (M3).
    """
    cleaned = re.sub(r'[\x00-\x1f\x7f"\\;]+', "_", filename or "document_filigrane.pdf").strip()
    ascii_fallback = cleaned.encode("ascii", errors="ignore").decode("ascii").strip() or "document_filigrane.pdf"
    quoted_utf8 = urllib.parse.quote(cleaned, safe="")
    return f'{disposition_type}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted_utf8}'


@app.get("/api/batches/{batch_id}/files/{file_id}/download")
async def download_single_file(batch_id: str, file_id: str) -> FileResponse:
    batch = BATCHES.get(batch_id)
    if not batch or file_id not in batch.files:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")

    item = batch.files[file_id]
    if item.status != "completed" or not item.output_path.exists():
        raise HTTPException(status_code=409, detail="Le fichier PDF filigrané n'est pas encore prêt.")

    return FileResponse(
        path=str(item.output_path),
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition_header(item.output_name, "attachment")},
    )


@app.get("/api/batches/{batch_id}/files/{file_id}/preview")
async def preview_single_file(batch_id: str, file_id: str) -> FileResponse:
    batch = BATCHES.get(batch_id)
    if not batch or file_id not in batch.files:
        raise HTTPException(status_code=404, detail="Fichier introuvable.")

    item = batch.files[file_id]
    if item.status != "completed" or not item.output_path.exists():
        raise HTTPException(status_code=409, detail="Le fichier PDF filigrané n'est pas encore prêt.")

    return FileResponse(
        path=str(item.output_path),
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition_header(item.output_name, "inline")},
    )


@app.get("/api/batches/{batch_id}/zip")
async def download_batch_zip(batch_id: str) -> Response:
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Lot introuvable.")

    completed_items = [
        batch.files[fid]
        for fid in batch.file_order
        if batch.files[fid].status == "completed" and batch.files[fid].output_path.exists()
    ]
    if not completed_items:
        raise HTTPException(status_code=409, detail="Aucun fichier terminé à inclure dans l'archive.")

    def _build_zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in completed_items:
                zf.write(item.output_path, arcname=item.output_name)
        return buf.getvalue()

    zip_bytes = await asyncio.to_thread(_build_zip)
    zip_filename = f"filigrane_lot_{batch_id}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition_header(zip_filename, "attachment")},
    )


async def _get_or_build_merged_pdf(batch: BatchJob) -> bytes:
    completed_paths = [
        batch.files[fid].output_path
        for fid in batch.file_order
        if batch.files[fid].status == "completed" and batch.files[fid].output_path.exists()
    ]
    if not completed_paths:
        raise HTTPException(status_code=409, detail="Aucun fichier terminé à fusionner.")

    return await asyncio.to_thread(merge_pdfs, completed_paths)


@app.get("/api/batches/{batch_id}/merged")
async def download_batch_merged(batch_id: str) -> Response:
    """Télécharge tous les PDF filigranés du lot fusionnés en un seul document PDF."""
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Lot introuvable.")

    merged_bytes = await _get_or_build_merged_pdf(batch)
    merged_filename = batch.get_merged_filename()
    return Response(
        content=merged_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition_header(merged_filename, "attachment")},
    )


@app.get("/api/batches/{batch_id}/merged/preview")
async def preview_batch_merged(batch_id: str) -> Response:
    """Aperçu dans le navigateur du PDF unique fusionnant tous les documents du lot."""
    batch = BATCHES.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Lot introuvable.")

    merged_bytes = await _get_or_build_merged_pdf(batch)
    merged_filename = batch.get_merged_filename()
    return Response(
        content=merged_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition_header(merged_filename, "inline")},
    )


@app.delete("/api/batches/{batch_id}")
async def delete_batch(batch_id: str) -> dict[str, str]:
    batch = BATCHES.pop(batch_id, None)
    if batch and batch.batch_dir.exists():
        await asyncio.to_thread(shutil.rmtree, batch.batch_dir, True)
    return {"deleted": batch_id}


# Permet aussi d'accéder directement à /styles.css, /app.js, /browser_engine.js (compatible GitHub Pages)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static_root")
