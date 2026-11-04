"""
Point d'entrée du projet Batch-Filigrane.
"""

from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serveur Web Batch FiligraneFacile (traitement PDF individuel en parallèle)."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Adresse d'écoute (défaut: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port d'écoute (défaut: 8000)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Ne pas ouvrir automatiquement le navigateur au démarrage",
    )
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print("\n============================================================")
    print(f"  Batch-Filigrane démarré sur : {url}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "  [AVERTISSEMENT SÉCURITÉ] Le serveur écoute sur une interface réseau non locale.\n"
            "  N'exposez pas ce serveur sur un réseau public non sécurisé."
        )
    print("============================================================\n")

    if not args.no_browser:
        def _open_browser() -> None:
            time.sleep(1.0)
            webbrowser.open(url)

        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run("batch_filigrane.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
