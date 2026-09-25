"""Aperçus des actus par capture d'écran, via le serveur MCP Playwright (@playwright/mcp).

Les cartes d'actus sans image (ni illustration, ni og:image) reçoivent une capture de la page
source : une seule session MCP (un navigateur Chromium headless, profil en mémoire) pour tout le
lot, pages visitées l'une après l'autre — borné par SCREENSHOT_MAX_PER_RUN pour la Jetson.

Garde-fous : seules des URL publiques passent (même contrôle SSRF que la Review) ; les images
sont enregistrées sous data/screenshots/<id>.jpeg (hors Git) et servies par l'API web.
Désactivé par défaut (SCREENSHOT_ENABLED) : nécessite Node.js et le navigateur de Playwright.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

# (url) -> octets JPEG ; remplaçable dans les tests
Capture = Callable[[str], Awaitable[bytes]]
MAX_BYTES = 2_000_000
MIN_BYTES = 15_000  # en deçà, un JPEG 1280×800 est une page quasi uniforme
# Navigateur de la version de Playwright embarquée par @playwright/mcp@0.0.82
INSTALL_HINT = "npx -y playwright@1.64.0-alpha-1789764292000 install chromium"


class ScreenshotError(RuntimeError):
    pass


@dataclass
class ScreenshotReport:
    saved: dict[str, str] = field(default_factory=dict)  # id d'actu → chemin du fichier
    errors: dict[str, str] = field(default_factory=dict)  # URL → erreur
    skipped: int = 0


def screenshot_path(item_id: str, directory: Path | None = None) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16}", item_id):
        raise ScreenshotError("Identifiant d'actu invalide.")
    return (directory or settings.screenshot_dir) / f"{item_id}.jpeg"


def server_params():
    from mcp import StdioServerParameters

    # Environnement minimal : aucun secret de l'application n'est transmis au navigateur.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "NVM_DIR", "DISPLAY") if key in os.environ}
    return StdioServerParameters(command=settings.screenshot_mcp_command, args=settings.screenshot_mcp_args, env=env)


async def _session_captures(urls: list[str], on_image: Callable[[str, bytes], None],
                            on_error: Callable[[str, str], None]) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), settings.screenshot_timeout)
            await session.call_tool("browser_resize", {"width": 1280, "height": 800})
            for url in urls:
                try:
                    navigation = await asyncio.wait_for(session.call_tool("browser_navigate", {"url": url}),
                                                        settings.screenshot_timeout)
                    if navigation.isError:
                        raise ScreenshotError(_text(navigation)[:200] or "navigation refusée")
                    # Pages rendues en JavaScript : laisser le contenu s'afficher avant la capture.
                    await session.call_tool("browser_wait_for", {"time": settings.screenshot_settle_seconds})
                    shot = await asyncio.wait_for(session.call_tool("browser_take_screenshot", {"type": "jpeg"}),
                                                  settings.screenshot_timeout)
                    image = next((block for block in shot.content if getattr(block, "type", "") == "image"), None)
                    if shot.isError or image is None:
                        raise ScreenshotError(_text(shot)[:200] or "aucune image renvoyée")
                    on_image(url, base64.b64decode(image.data))
                except Exception as error:  # noqa: BLE001 — une page en échec n'arrête pas le lot
                    on_error(url, f"{type(error).__name__} : {error}")
            await session.call_tool("browser_close", {})


def _text(result) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


def capture_news(items: list[dict], directory: Path | None = None, limit: int | None = None,
                 capture: Callable[[list[str], Callable, Callable], Awaitable[None]] | None = None,
                 check: Callable[[str], str] | None = None) -> ScreenshotReport:
    """Capture les actus sans image (au plus `limit`), enregistre les JPEG, renvoie le rapport."""
    from app.mcp_client import run_mcp
    from app.review import FetchError, check_url

    directory = directory or settings.screenshot_dir
    directory.mkdir(parents=True, exist_ok=True)
    report = ScreenshotReport()
    wanted: dict[str, str] = {}  # URL → id
    for item in items:
        if item.get("image") or item.get("screenshot") or screenshot_path(item["id"], directory).exists():
            report.skipped += 1
            continue
        try:
            (check or check_url)(item["url"])  # SSRF : adresse publique uniquement
        except FetchError as error:
            report.errors[item["url"]] = str(error)
            continue
        wanted[item["url"]] = item["id"]
        if len(wanted) >= (limit or settings.screenshot_max_per_run):
            break
    if not wanted:
        return report

    def on_image(url: str, data: bytes) -> None:
        if not data.startswith(b"\xff\xd8") or len(data) > MAX_BYTES:  # JPEG attendu, taille bornée
            report.errors[url] = "image invalide ou trop lourde"
            return
        if len(data) < MIN_BYTES:  # page quasi blanche : écran de chargement, anti-robot…
            report.errors[url] = "page vide ou protégée (capture écartée)"
            return
        path = screenshot_path(wanted[url], directory)
        path.write_bytes(data)
        report.saved[wanted[url]] = str(path)

    def on_error(url: str, message: str) -> None:
        report.errors[url] = message

    try:
        run_mcp((capture or _session_captures)(list(wanted), on_image, on_error))
    except Exception as error:  # noqa: BLE001 — serveur MCP absent, navigateur non installé…
        raise ScreenshotError(f"Serveur MCP Playwright indisponible : {error}. Installer le navigateur : "
                              f"{INSTALL_HINT}") from error
    return report
