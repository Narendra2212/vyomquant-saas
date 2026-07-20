with open('test_live_telemetry_pipeline.py', 'r') as f:
    c = f.read()

if "import os" not in c:
    c = "import os\nos.environ['AERORA_MODE'] = 'paper'\n" + c

with open('test_live_telemetry_pipeline.py', 'w') as f:
    f.write(c)
