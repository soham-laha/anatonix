#!/usr/bin/env python3
"""Optional end-to-end smoke test for Anatonix.

Drives a real browser against a running Anatonix instance using Chromium's
fake-camera support, so the complete pipeline (component handshake, MediaPipe
models, calibration, telemetry persistence) can be verified without a physical
webcam.

Requirements
------------
    python -m pip install playwright
    python -m playwright install chromium

A test video is required. Record or synthesise a small Y4M/MJPEG clip of a
person at a desk and pass it with ``--feed``. Chrome accepts:

    --feed /path/to/face.y4m          (YUV4MPEG2, e.g. 640x480 @ 15 fps)
    --feed /path/to/face.mjpeg        (concatenated JPEG frames)

Usage
-----
    # terminal 1
    python -m streamlit run app.py --server.port 8700

    # terminal 2
    python tools/browser_smoke_test.py --feed /tmp/face.y4m --url http://localhost:8700

The script prints a PASS/FAIL line per checkpoint and exits non-zero if any
checkpoint fails. It never touches your real camera.
"""

from __future__ import annotations

import argparse
import re
import sys
import time

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--use-gl=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--autoplay-policy=no-user-gesture-required",
]

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    return ok


def poll(frame, expression: str, timeout: float = 12.0, interval: float = 0.5):
    """Evaluate ``expression`` repeatedly until it is truthy (slow sandboxes need this)."""
    deadline = time.time() + timeout
    value = None
    while time.time() < deadline:
        try:
            value = frame.evaluate(expression)
        except Exception:  # noqa: BLE001 - frame may be navigating
            value = None
        if value:
            return value
        time.sleep(interval)
    return value


def click(frame, selector: str) -> None:
    """Click a control.

    A synthesised mouse click can land on a transient overlay (toast, closing
    dialog) when the machine is slow, so the element's own click handler is
    invoked directly — it still exercises the real listener.
    """
    frame.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) throw new Error('control not found: ' + sel);
            el.scrollIntoView({ block: 'center' });
            el.click();
        }""",
        selector,
    )
    time.sleep(0.2)


def text(frame, selector: str) -> str:
    try:
        return (frame.evaluate(
            f"document.querySelector({selector!r})?.textContent?.trim() ?? ''"
        ) or "").strip()
    except Exception:  # noqa: BLE001 - diagnostics only
        return ""


def run(url: str, feed: str, calibration: bool, settle: float) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=CHROMIUM_ARGS + [f"--use-file-for-fake-video-capture={feed}"])
        context = browser.new_context(viewport={"width": 1760, "height": 1100}, permissions=["camera"])
        page = context.new_page()
        console_errors: list[str] = []
        page.on("pageerror", lambda exc: console_errors.append(str(exc)[:200]))

        # ---------------------------------------------------------- pages
        for name in ("dashboard", "monitoring", "analytics", "reports", "settings"):
            page.goto(f"{url}/?page={name}", wait_until="load")
            time.sleep(3)
            body = page.inner_text("body")
            check(f"page {name} renders", "could not be rendered" not in body)

        # -------------------------------------------------- monitoring flow
        page.goto(f"{url}/?page=monitoring", wait_until="load")
        time.sleep(5)
        iframe = page.query_selector("iframe[src*='anatonix_monitoring']")
        if not check("component iframe mounted", iframe is not None):
            browser.close()
            return 1
        frame = iframe.content_frame()
        click(frame, "#btnInit")
        started = poll(
            frame,
            "(() => { const s = document.getElementById('chipStateText').textContent.trim();"
            " return s && s !== 'Standby' && s !== 'Initialising' ? s : ''; })()",
            timeout=max(settle, 45),
        )
        if not started:
            print("      (still reporting:", text(frame, "#chipStateText"), ")")

        state = text(frame, "#chipStateText")
        check("camera started", "Tracking" in state or "Watching" in state, state)

        lean = text(frame, "#hudLean")
        check("live lean reported", lean not in ("", "--"), lean)

        confidence = text(frame, "#chipConfidence")
        check("tracking confidence reported", confidence not in ("", "--"), confidence)

        alert_hidden = frame.evaluate("document.getElementById('alertBanner').hidden")
        check("no stale alert banner on a calm frame", bool(alert_hidden))

        eye_crop = frame.evaluate(
            "(() => { const c = document.getElementById('eyeCanvas');"
            " if (!c) return -1; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
            " let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] + d[i+1] + d[i+2] > 40) n++; return n; })()"
        )
        check("ocular crop canvas painted", eye_crop > 1000, f"{eye_crop} px")

        if calibration:
            click(frame, "#btnCalibrate")
            check("calibration dialog opens", bool(poll(
                frame, "!document.getElementById('calibModal').classList.contains('is-hidden')", 6)))
            click(frame, "#btnCalibStart")
            status = poll(
                frame,
                "(() => { const t = document.getElementById('calibStatus').textContent.trim();"
                " return t === 'Calibrated' ? t : ''; })()",
                timeout=90,
            )
            check("calibration stored", status == "Calibrated", text(frame, "#calibStatus"))

        click(frame, "#btnPause")
        check("pause works", bool(poll(
            frame, "document.getElementById('pauseLabel').textContent.trim() === 'Resume'", 8)),
            text(frame, "#chipStateText"))
        click(frame, "#btnPause")
        check("resume works", bool(poll(
            frame, "document.getElementById('pauseLabel').textContent.trim() === 'Pause'", 8)))

        click(frame, "#btnStop")
        poll(frame, "!document.getElementById('standby').classList.contains('is-hidden')", 15)
        released = poll(
            frame,
            "(() => { const v = document.getElementById('videoFeed');"
            " return !v.srcObject || v.srcObject.getTracks().every(t => t.readyState === 'ended'); })()",
            15,
        )
        check("camera released on stop", bool(released))
        check("standby overlay returned", not frame.evaluate(
            "document.getElementById('standby').classList.contains('is-hidden')"))

        # ----------------------------------------------- persisted data
        page.goto(f"{url}/?page=reports", wait_until="load")
        time.sleep(5)
        body = page.inner_text("body").upper()
        match = re.search(r"(\d+)\s+SESSIONS?\s+STORED", body)
        recorded = bool(match) and int(match.group(1)) > 0
        check("session recorded in the database", recorded, match.group(0) if match else "no session counter")

        check("no uncaught page errors", not console_errors, "; ".join(console_errors[:2]))
        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Anatonix browser smoke test")
    parser.add_argument("--url", default="http://localhost:8700", help="running Anatonix instance")
    parser.add_argument("--feed", required=True, help="path to a .y4m or .mjpeg test video")
    parser.add_argument("--no-calibration", action="store_true", help="skip the calibration step")
    parser.add_argument(
        "--settle",
        type=float,
        default=30.0,
        help="seconds to wait for model startup (models are downloaded on first run)",
    )
    args = parser.parse_args()
    return run(args.url.rstrip("/"), args.feed, not args.no_calibration, args.settle)


if __name__ == "__main__":
    sys.exit(main())
