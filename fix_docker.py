import os

replacements = {
    "http://localhost:9200/_cluster/health": "http://elasticsearch:9200/_cluster/health",
    "http://localhost:5601/api/status": "http://kibana:5601/api/status",
    "http://localhost:9093": "http://alertmanager:9093",
    "http://localhost:8000/health/live": "http://backend:8000/health/live",
    "http://localhost:8000/health": "http://backend:8000/health",
    "http://localhost:8080/health": "http://api:8080/health",
    "http://localhost:3001": "http://grafana:3001",
    "http://localhost/nginx-health": "http://nginx/nginx-health",
    "http://localhost:8002/health/live": "http://websocket:8002/health/live"
}

def fix_docker_files():
    for root, dirs, files in os.walk(r"d:\aerora_quant_backend_updated_final1"):
        for file in files:
            if file.startswith("docker-compose") and file.endswith(".yml"):
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                new_content = content
                for old, new in replacements.items():
                    new_content = new_content.replace(old, new)
                
                if new_content != content:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"Fixed {file}")

if __name__ == "__main__":
    fix_docker_files()
