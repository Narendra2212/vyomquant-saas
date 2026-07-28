"""
tests/test_tauri_csp_configuration.py

Unit tests verifying Tauri desktop application's Content-Security-Policy.
"""

import json
import os
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
