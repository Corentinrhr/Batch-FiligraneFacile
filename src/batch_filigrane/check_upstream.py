"""
Outil de suivi et de comparaison avec les fichiers sources officiels de DossierFacile / FiligraneFacile
sur les dépôts GitHub du Ministère (MTES-MCT).

Usage :
  uv run check-upstream          # Vérifie si MTES-MCT a modifié les fichiers sources et affiche le diff
  uv run check-upstream --sync   # Met à jour les fichiers de référence locaux et upstream/manifest.json
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_DIR = PROJECT_ROOT / "upstream"
REFERENCE_DIR = UPSTREAM_DIR / "reference"
MANIFEST_PATH = UPSTREAM_DIR / "manifest.json"

TRACKED_UPSTREAM_FILES: list[dict[str, str]] = [
    {
        "id": "BOPdfDocumentTemplate.java",
        "repo": "MTES-MCT/dossierfacile-backend",
        "branch": "main",
        "remote_path": "dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/service/templates/BOPdfDocumentTemplate.java",
        "local_target": "src/batch_filigrane/watermark_engine.py",
        "description": "Algorithme principal du filigrane (rastérisation PDF/images, cadrage A4, rotation -25°, flou gaussien, couleurs et détourage QR codes)",
    },
    {
        "id": "DFFilter.java",
        "repo": "MTES-MCT/dossierfacile-backend",
        "branch": "main",
        "remote_path": "dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/service/templates/DFFilter.java",
        "local_target": "src/batch_filigrane/watermark_engine.py (apply_df_distortion_filter)",
        "description": "Filtre d'ondulation sinusoïdale anti-OCR (xFrequency=12, yFrequency=8, maxDistorsion=28)",
    },
    {
        "id": "PdfTemplateParameters.java",
        "repo": "MTES-MCT/dossierfacile-backend",
        "branch": "main",
        "remote_path": "dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/model/PdfTemplateParameters.java",
        "local_target": "src/batch_filigrane/watermark_engine.py (WatermarkOptions)",
        "description": "Paramètres PDF par défaut (PDRectangle.A4, compressionQuality=0.9f, maxPage=A4150)",
    },
    {
        "id": "PageDimension.java",
        "repo": "MTES-MCT/dossierfacile-backend",
        "branch": "main",
        "remote_path": "dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/model/PageDimension.java",
        "local_target": "src/batch_filigrane/watermark_engine.py (PAGE_DIMENSIONS)",
        "description": "Résolutions A4 (A4128: 1058x1496 @ 128 DPI, A4150: 1240x1754 @ 150 DPI)",
    },
    {
        "id": "FeatureFlipping.java",
        "repo": "MTES-MCT/dossierfacile-backend",
        "branch": "main",
        "remote_path": "dossierfacile-pdf-generator/src/main/java/fr/dossierfacile/api/pdfgenerator/configuration/FeatureFlipping.java",
        "local_target": "src/batch_filigrane/watermark_engine.py (use_colors, use_distortion)",
        "description": "Options d'activation des couleurs multiples et de la distorsion",
    },
    {
        "id": "WatermarkingPage.vue",
        "repo": "MTES-MCT/Dossier-Facile-Frontend",
        "branch": "main",
        "remote_path": "watermark/src/views/WatermarkingPage.vue",
        "local_target": "src/batch_filigrane/static/index.html & app.js",
        "description": "Interface utilisateur officielle FiligraneFacile (limites de caractères, nommage _filigrane)",
    },
]


def _github_blob_url(entry: dict[str, str]) -> str:
    return f"https://github.com/{entry['repo']}/blob/{entry['branch']}/{entry['remote_path']}"


def _github_raw_url(entry: dict[str, str]) -> str:
    return f"https://raw.githubusercontent.com/{entry['repo']}/{entry['branch']}/{entry['remote_path']}"


def _sha256_text(content: str) -> str:
    normalized = content.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sync_upstream() -> int:
    UPSTREAM_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    manifest_entries: list[dict[str, str]] = []
    print("Synchronisation des fichiers sources depuis MTES-MCT (GitHub)...")

    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for entry in TRACKED_UPSTREAM_FILES:
            raw_url = _github_raw_url(entry)
            resp = client.get(raw_url)
            resp.raise_for_status()
            content = resp.text.replace("\r\n", "\n")
            sha = _sha256_text(content)

            ref_file = REFERENCE_DIR / entry["id"]
            ref_file.write_text(content, encoding="utf-8")

            manifest_entries.append(
                {
                    **entry,
                    "github_url": _github_blob_url(entry),
                    "raw_url": raw_url,
                    "sha256": sha,
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            print(f"  [OK] {entry['id']} ({sha[:12]}) -> {entry['local_target']}")

    manifest_data = {
        "description": "Suivi des fichiers sources officiels DossierFacile / FiligraneFacile (MTES-MCT)",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": manifest_entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nManifeste mis à jour dans : {MANIFEST_PATH}")
    return 0


def check_upstream(show_diff: bool = True) -> int:
    if not MANIFEST_PATH.exists():
        print("Aucun manifeste local trouvé. Initialisation avec --sync...")
        return sync_upstream()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tracked_by_id = {f["id"]: f for f in manifest.get("files", [])}

    changed_count = 0
    print("Vérification des mises à jour amont sur GitHub (MTES-MCT)...\n")

    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for entry in TRACKED_UPSTREAM_FILES:
            fid = entry["id"]
            raw_url = _github_raw_url(entry)
            blob_url = _github_blob_url(entry)
            resp = client.get(raw_url)
            resp.raise_for_status()

            remote_content = resp.text.replace("\r\n", "\n")
            remote_sha = _sha256_text(remote_content)

            recorded = tracked_by_id.get(fid, {})
            local_sha = recorded.get("sha256")
            ref_file = REFERENCE_DIR / fid

            if local_sha == remote_sha:
                print(f"  [À JOUR]  {fid:<30} ({remote_sha[:10]}) -> {entry['local_target']}")
            else:
                changed_count += 1
                print(f"  [MODIFIÉ] {fid:<30} (local: {str(local_sha)[:10]} != distant: {remote_sha[:10]})")
                print(f"            Source GitHub : {blob_url}")
                print(f"            Impact local  : {entry['local_target']}")

                if show_diff and ref_file.exists():
                    old_lines = ref_file.read_text(encoding="utf-8").replace("\r\n", "\n").splitlines()
                    new_lines = remote_content.splitlines()
                    diff = difflib.unified_diff(
                        old_lines,
                        new_lines,
                        fromfile=f"a/{fid} (référence locale)",
                        tofile=f"b/{fid} (GitHub MTES-MCT)",
                        lineterm="",
                    )
                    print("\n--- Différence détectée ---")
                    for line in list(diff)[:120]:
                        print(f"    {line}")
                    print("---------------------------\n")

    if changed_count == 0:
        print("\n[OK] Tous les fichiers portes sont strictement a jour avec les depots MTES-MCT.")
        return 0

    print(
        f"\n[ATTENTION] {changed_count} fichier(s) amont ont evolue sur MTES-MCT. "
        "Apres avoir repercute les changements dans watermark_engine.py, lancez `uv run check-upstream --sync`."
    )
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vérifie si les fichiers sources de DossierFacile / FiligraneFacile (MTES-MCT) ont été modifiés."
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Télécharge la dernière version de référence des fichiers MTES-MCT et met à jour upstream/manifest.json",
    )
    parser.add_argument(
        "--no-diff",
        action="store_true",
        help="Ne pas afficher le diff détaillé en cas de modification amont",
    )
    args = parser.parse_args()

    if args.sync:
        sys.exit(sync_upstream())
    else:
        sys.exit(check_upstream(show_diff=not args.no_diff))


if __name__ == "__main__":
    main()
