"""
tests/test_tauri_csp_configuration.py

Unit tests verifying Tauri desktop application's Content-Security-Policy.
"""

import json
import os
import re

import pytest

TAURI_CONF_PATH = 'algo22-terminal/src-tauri/tauri.conf.json'


def test_tauri_csp_configured_and_not_null():
    assert os.path.exists(TAURI_CONF_PATH), f'Failed to find {TAURI_CONF_PATH}'
    
    with open(TAURI_CONF_PATH, encoding='utf-8') as f:
        config = json.load(f)
    
    csp = config.get('app', {}).get('security', {}).get('csp')
    assert csp is not None, 'Tauri CSP must not be null'
    assert isinstance(csp, str), 'Tauri CSP must be a string'
    assert len(csp.strip()) > 0, 'Tauri CSP must not be empty'



def test_tauri_csp_directives_strictness():
    with open(TAURI_CONF_PATH, encoding='utf-8') as f:
        config = json.load(f)
    
    csp = config.get('app', {}).get('security', {}).get('csp', '')
    
    directives = {}
    for part in csp.split(';'):
        part = part.strip()
        if part and ' ' in part:
            k, v = part.split(' ', 1)
            directives[k.strip()] = v.strip()
    
    script_src = directives.get('script-src', '')
    connect_src = directives.get('connect-src', '')
    
    # 1. script-src must not contain unsafe-inline or unsafe-eval
    assert "'unsafe-inline'" not in script_src, 'Tauri script-src must not allow unsafe-inline'
    assert "'unsafe-eval'" not in script_src, 'Tauri script-src must not allow unsafe-eval'
    
    # 2. script-src must include tauri: and ipc: for IPC
    assert 'tauri:' in script_src, 'Tauri script-src must include tauri:'
    assert 'ipc:' in script_src, 'Tauri script-src must include ipc:'
    
    # 3. connect-src must not contain unscoped http: or https: wildcards
    connect_tokens = connect_src.split()
    assert 'http:' not in connect_tokens, 'Tauri connect-src must not allow all http:'
    assert 'https:' not in connect_tokens, 'Tauri connect-src must not allow all https:'


# ══════════════════════════════════════════════════════════════════════════════════════
#  THE PAYMENT ORIGINS, AND WHY THE DESKTOP POLICY IS COMPARED WITH THE WEB ONE
# ══════════════════════════════════════════════════════════════════════════════════════
#
# The tests above check that the desktop CSP is STRICT. They say nothing about whether it
# permits the origins the app actually needs, or forbids the ones it must not, and that gap
# let the desktop policy drift away from the web policy in three separate ways at once:
#
#   1. script-src allowed https://js.stripe.com and connect-src allowed
#      https://api.stripe.com. Stripe has been removed from the product in full -- no
#      webhook route, no checkout branch, no SDK in requirements -- so these granted script
#      execution and network access to a third party for code that cannot run. index.html
#      had already reasoned its way to excluding them ("granting Stripe origins here would
#      be a permission for a path that cannot be reached"); this file had not.
#
#   2. connect-src omitted https://lumberjack.razorpay.com, which the Razorpay checkout
#      bundle beacons to. The web policy (index.html and main.py's response header) was
#      corrected to allow it; the desktop policy was not.
#
#   3. There was NO frame-src directive at all. Razorpay Standard Checkout renders as a
#      MODAL IFRAME -- which is precisely why index.html carries
#      `frame-src https://checkout.razorpay.com`. frame-src falls back to child-src and then
#      to default-src, and default-src here is `'self' tauri: ipc:`, so the checkout modal
#      would have been blocked in the desktop build. Allowing the script that OPENS checkout
#      while forbidding the frame it opens is the kind of half-permission that fails only at
#      the moment a customer tries to pay.
#
# So the assertions below are about parity with the web policy, not about strictness in the
# abstract: these two documents describe the same application and must agree about who it
# talks to.

WEB_INDEX_HTML = 'algo22-terminal/index.html'


def _tauri_directives():
    with open(TAURI_CONF_PATH, encoding='utf-8') as f:
        config = json.load(f)
    csp = config.get('app', {}).get('security', {}).get('csp', '')
    directives = {}
    for part in csp.split(';'):
        part = part.strip()
        if part and ' ' in part:
            k, v = part.split(' ', 1)
            directives[k.strip()] = v.strip().split()
    return csp, directives


def test_no_stripe_origin_survives_in_the_desktop_policy():
    """Razorpay is the only gateway, so no Stripe origin may hold a privilege here."""
    csp, directives = _tauri_directives()

    offenders = []
    for name, tokens in directives.items():
        for token in tokens:
            if 'stripe.com' in token:
                offenders.append(f'{name} allows {token}')

    assert offenders == [], (
        'the desktop CSP grants a privilege to a Stripe origin:\n  '
        + '\n  '.join(offenders)
        + '\nStripe was removed from the product in full, so this is a permission for a '
          'path that cannot be reached.'
    )


def test_the_razorpay_checkout_frame_is_permitted():
    """Standard Checkout is a modal IFRAME, so the script permission alone is not enough."""
    csp, directives = _tauri_directives()

    assert 'frame-src' in directives, (
        "the desktop CSP has no frame-src, so frames fall back to default-src "
        f"({' '.join(directives.get('default-src', []))}) -- which does not include "
        'checkout.razorpay.com, so the Razorpay checkout modal cannot open in the desktop '
        'build. index.html carries `frame-src https://checkout.razorpay.com` for exactly '
        'this reason.'
    )
    assert 'https://checkout.razorpay.com' in directives['frame-src'], (
        f"frame-src is {directives['frame-src']}, which does not permit the Razorpay "
        'checkout modal.'
    )
    # And the script that opens it, since allowing one without the other fails at payment.
    assert 'https://checkout.razorpay.com' in directives.get('script-src', []), (
        'script-src does not permit the Razorpay checkout bundle.'
    )


def test_the_desktop_and_web_policies_agree_on_the_razorpay_origins():
    """Two documents describing one application must not disagree about who it talks to.

    Compared against index.html rather than restated as a literal list: a hardcoded
    expectation here would be a third place to keep in step, and would go stale the next
    time the web policy legitimately changes.
    """
    _csp, directives = _tauri_directives()

    with open(WEB_INDEX_HTML, encoding='utf-8') as f:
        web = f.read()

    # Every razorpay origin the WEB policy permits must also be permitted on the desktop.
    web_origins = set(re.findall(r'https://[A-Za-z0-9.\-]*razorpay\.com', web))
    assert web_origins, 'no razorpay origin found in index.html; repoint this test'

    desktop_origins = {
        token for tokens in directives.values() for token in tokens if 'razorpay.com' in token
    }

    missing = sorted(web_origins - desktop_origins)
    assert missing == [], (
        f'the web CSP permits {missing} but the desktop CSP does not. The desktop build '
        'runs the same bundle, so an origin the bundle contacts and the policy forbids is a '
        'silent CSP violation at the moment a customer pays.'
    )
