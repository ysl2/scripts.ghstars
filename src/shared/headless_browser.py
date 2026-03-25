import asyncio
import os
import shutil
from pathlib import Path


DEFAULT_CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def resolve_chrome_binary(chrome_binary: str | None = None) -> str:
    candidate = (chrome_binary or os.environ.get("GOOGLE_CHROME_BIN") or DEFAULT_CHROME_BINARY).strip()
    if not candidate:
        raise ValueError("Google Chrome binary is not configured")

    if Path(candidate).exists():
        return candidate

    resolved = shutil.which(candidate)
    if resolved:
        return resolved

    raise ValueError(f"Google Chrome binary not found: {candidate}")


async def dump_rendered_html(
    url: str,
    *,
    chrome_binary: str | None = None,
    virtual_time_budget_ms: int = 5000,
    timeout_seconds: float = 20.0,
) -> str:
    binary = resolve_chrome_binary(chrome_binary)
    process = await asyncio.create_subprocess_exec(
        binary,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        f"--virtual-time-budget={virtual_time_budget_ms}",
        "--dump-dom",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise

    if process.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise ValueError(detail or f"Chrome exited with code {process.returncode}")

    html_text = (stdout or b"").decode("utf-8", errors="replace")
    if not html_text.strip():
        raise ValueError("Chrome returned empty HTML")

    return html_text
