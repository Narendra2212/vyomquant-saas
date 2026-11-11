"""PAY-01: the Razorpay checkout modal opened as a blank page with the browser's "blocked" icon.

Razorpay Standard Checkout loads its script from https://checkout.razorpay.com/v1/checkout.js,
but the modal IFRAME that script opens is served from https://api.razorpay.com/v1/checkout/public.
The SPA's CSP (index.html <meta>, the policy production actually enforces - the SPA is served from
S3 via CloudFront) only allowed `frame-src https://checkout.razorpay.com`, so Chrome refused the
frame: "Refused to frame 'https://api.razorpay.com/' because it violates ... frame-src". Every
plan upgrade and marketplace purchase was therefore impossible to pay for. Reproduced in headless
Chromium against the real checkout.js before the fix, and the frame loaded with no violation after.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_FRAME_ORIGIN = "https://api.razorpay.com"


def _directives(policy: str) -> dict:
    out = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = tokens[1:]
    return out


def _web_policy() -> str:
    html = (ROOT / "algo22-terminal" / "index.html").read_text()
    match = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]*)"', html)
    assert match, "index.html no longer carries a CSP <meta>; repoint this test"
    return match.group(1)


def _desktop_policy() -> str:
    conf = json.loads((ROOT / "algo22-terminal" / "src-tauri" / "tauri.conf.json").read_text())
    return conf["app"]["security"]["csp"]


def test_web_policy_permits_the_checkout_iframe_origin():
    frame_src = _directives(_web_policy()).get("frame-src", [])
    assert CHECKOUT_FRAME_ORIGIN in frame_src, (
        f"frame-src is {frame_src}; the Razorpay checkout modal is an iframe on "
        f"{CHECKOUT_FRAME_ORIGIN} and will render blank (blocked) without it"
    )


def test_desktop_policy_permits_the_checkout_iframe_origin():
    frame_src = _directives(_desktop_policy()).get("frame-src", [])
    assert CHECKOUT_FRAME_ORIGIN in frame_src, f"desktop frame-src is {frame_src}"
