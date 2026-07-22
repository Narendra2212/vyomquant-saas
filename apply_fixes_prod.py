import os
import re

def update_requirements(filepath):
    if not os.path.exists(filepath):
        return
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # The STRICTLY required dependencies for production startup
    deps_to_add = [
        "aiosmtplib==3.0.1",
        "asyncpg==0.29.0",
        "PyJWT==2.13.0",
        "rq==1.16.2",
        "scipy==1.14.0",
        "slowapi==0.1.9",
        "sqlalchemy==2.0.35",
        "psycopg2-binary==2.9.9",
        "prometheus-client==0.20.0"
    ]

    for dep in deps_to_add:
        pkg_name = dep.split('==')[0].split('>=')[0].split('[')[0]
        # Regex to check if package is in file
        if not re.search(rf'^{pkg_name}[>=<\[]', content, flags=re.MULTILINE|re.IGNORECASE) and not re.search(rf'^{pkg_name}$', content, flags=re.MULTILINE|re.IGNORECASE):
            content += f"\n{dep}"

    # Remove the older prometheus-client>=0.17.0 if present to avoid conflicts with 0.20.0
    if 'prometheus-client>=0.17.0' in content and 'prometheus-client==0.20.0' in content:
        content = content.replace('prometheus-client>=0.17.0', '')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content.strip() + "\n")

update_requirements('requirements.txt')
update_requirements('backend_app/requirements.txt')

def fix_imports():
    # 1. rate_limit_middleware.py
    fpath = 'backend_app/core/rate_limit_middleware.py'
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        content = content.replace('aerora_quant_backend_updated_final1.core.rate_limiter', 'backend_app.core.rate_limiter')
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)

    # 2. rate_limiter.py
    fpath = 'backend_app/core/rate_limiter.py'
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        content = content.replace('aerora_quant_backend_updated_final1.core.cache', 'backend_app.core.cache')
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)

    # 3. strategies.py
    fpath = 'backend_app/routers/strategies.py'
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        content = content.replace('from connection_engine import ConnectionEngine', 'from backend_app.backend.connection_engine import ConnectionEngine')
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)

    # 4. ws_event_stream.py
    fpath = 'backend_app/backend/ws_event_stream.py'
    if os.path.exists(fpath):
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        content = content.replace('from ws_channels import ChannelType, EventType, is_valid_channel', 'from backend_app.backend.ws_channels import ChannelType, EventType, is_valid_channel')
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)

fix_imports()
print("Done fixing strictly required dependencies and imports.")
