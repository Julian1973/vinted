"""Runway API: preview the reshoot before you buy.

The flip's whole bet is that a clean shot on a plain background closes the gap
to the market median. This module asks Runway's image model to restage a
listing photo as that clean studio shot, so you can see the "after" next to
the "before" in the report — and, once you own the item, clean up your own
photos the same way.

API reference: https://docs.dev.runwayml.com/api/
Auth: set RUNWAYML_API_SECRET (keys from https://dev.runway.com).

Generation is asynchronous: POST starts a task, then GET /v1/tasks/{id} is
polled until SUCCEEDED (output URLs, which expire in 24-48h — so we download
them immediately) or FAILED.

Standalone use on your own photo:

    python -m vinted_flip.runway my_photo.jpg -o clean.jpg
"""

from __future__ import annotations

import argparse
import base64
import io
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

log = logging.getLogger(__name__)

API_BASE = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"

# gen4_image_turbo: 2 credits ($0.02) per image at any resolution, and it
# requires a reference image — exactly our case. gen4_image is the higher
# quality option (5 credits at 720p, 8 at 1080p).
DEFAULT_MODEL = "gen4_image_turbo"
MODELS = ("gen4_image_turbo", "gen4_image")
# Vinted's grid is portrait 3:4.
DEFAULT_RATIO = "1080:1440"

MAX_DATA_URI_BYTES = 5 * 1024 * 1024

# "@item" in the prompt refers to the reference image tagged "item". The
# prompt is strict about fidelity on purpose: a buyer must receive what the
# photo shows, so the model may change the staging, never the garment.
RESHOOT_PROMPT = (
    "Professional resale product photo of the exact item from @item, "
    "neatly laid flat and centred on a plain light-grey seamless background. "
    "Soft, even daylight studio lighting, no harsh shadows, sharp focus, true "
    "colours, whole item in frame. Keep the item identical to @item: same "
    "colour, logos, text, pattern, fabric texture, fit, and any wear or marks. "
    "Do not add, remove or alter anything on the item. No people, no props, "
    "no text overlays."
)


class RunwayError(RuntimeError):
    pass


class RunwayClient:
    def __init__(self, api_key: Optional[str] = None, *, timeout: float = 30.0):
        api_key = api_key or os.environ.get("RUNWAYML_API_SECRET")
        if not api_key:
            raise RunwayError(
                "No Runway API key: set RUNWAYML_API_SECRET "
                "(create one at https://dev.runway.com)."
            )
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "X-Runway-Version": API_VERSION,
        })

    def _request(self, method: str, path: str, **kwargs) -> dict:
        # 429 means the org is over its rate limit; back off and retry.
        for attempt in range(5):
            resp = self.session.request(
                method, API_BASE + path, timeout=self.timeout, **kwargs
            )
            if resp.status_code == 429:
                wait = 2 ** attempt * 2
                log.debug("Runway rate-limited; retrying in %ds", wait)
                time.sleep(wait)
                continue
            if not resp.ok:
                raise RunwayError(
                    f"{method} {path} → HTTP {resp.status_code}: {resp.text[:300]}"
                )
            return resp.json() if resp.content else {}
        raise RunwayError(f"{method} {path} still rate-limited after retries")

    def text_to_image(
        self,
        prompt: str,
        reference_uri: str,
        *,
        tag: str = "item",
        model: str = DEFAULT_MODEL,
        ratio: str = DEFAULT_RATIO,
        seed: Optional[int] = None,
    ) -> str:
        """Start a POST /v1/text_to_image task; returns the task id."""
        body: dict = {
            "model": model,
            "promptText": prompt,
            "ratio": ratio,
            "referenceImages": [{"uri": reference_uri, "tag": tag}],
        }
        if seed is not None:
            body["seed"] = seed
        task = self._request("POST", "/v1/text_to_image", json=body)
        log.debug("Runway task %s started (est. %s credits)", task.get("id"),
                  task.get("estimatedCost", {}).get("credits"))
        return task["id"]

    def wait_for_output(self, task_id: str, *, max_wait: float = 300.0) -> list[str]:
        """Poll GET /v1/tasks/{id} with backoff until the task is terminal."""
        deadline = time.monotonic() + max_wait
        delay = 2.0
        while True:
            task = self._request("GET", f"/v1/tasks/{task_id}")
            status = task.get("status")
            if status == "SUCCEEDED":
                return task.get("output") or []
            if status == "FAILED":
                raise RunwayError(
                    f"Task {task_id} failed ({task.get('failureCode')}): "
                    f"{task.get('failure')}"
                )
            if status == "CANCELLED":
                raise RunwayError(f"Task {task_id} was cancelled")
            # PENDING / THROTTLED / RUNNING: keep waiting.
            if time.monotonic() > deadline:
                raise RunwayError(f"Task {task_id} still {status} after {max_wait:.0f}s")
            time.sleep(delay)
            delay = min(delay * 1.5, 10.0)

    def download(self, url: str) -> bytes:
        # Output URLs are pre-signed; don't send our API key to them.
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        return resp.content

    def reshoot(self, image: bytes, *, model: str = DEFAULT_MODEL,
                ratio: str = DEFAULT_RATIO) -> bytes:
        """Restage a listing photo as a clean studio shot; returns image bytes."""
        task_id = self.text_to_image(
            RESHOOT_PROMPT, to_data_uri(image), model=model, ratio=ratio
        )
        output = self.wait_for_output(task_id)
        if not output:
            raise RunwayError(f"Task {task_id} succeeded with no output")
        # Normalise to JPEG so callers can name files .jpg whatever Runway sent.
        img = Image.open(io.BytesIO(self.download(output[0]))).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        return buf.getvalue()


def to_data_uri(image: bytes) -> str:
    """Encode as a JPEG data URI under Runway's 5MB inline limit.

    Sending bytes inline rather than the Vinted photo URL means Runway never
    has to fetch from Vinted's CDN, which may refuse it.
    """
    img = Image.open(io.BytesIO(image)).convert("RGB")
    size = 2048
    while True:
        img.thumbnail((size, size))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        # base64 inflates by 4/3.
        if len(buf.getvalue()) * 4 // 3 < MAX_DATA_URI_BYTES - 100 or size <= 512:
            break
        size //= 2
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vinted_flip.runway",
        description="Restage a garment photo as a clean studio shot via Runway.",
    )
    parser.add_argument("photo", help="Path to your photo of the item.")
    parser.add_argument("-o", "--out", default=None,
                        help="Output path (default: <photo>-studio.jpg).")
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    src = Path(args.photo)
    out = Path(args.out) if args.out else src.with_name(f"{src.stem}-studio.jpg")
    try:
        data = RunwayClient().reshoot(src.read_bytes(), model=args.model)
    except RunwayError as exc:
        log.error("%s", exc)
        return 1
    out.write_bytes(data)
    log.info("Studio shot → %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
