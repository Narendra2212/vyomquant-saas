import json
import subprocess

# 1. Fetch current task definition
res = subprocess.run(
    ["aws", "ecs", "describe-task-definition", "--task-definition", "vyomquant-api", "--region", "ap-southeast-1"],
    capture_output=True, text=True, check=True
)
data = json.loads(res.stdout)
td = data["taskDefinition"]

# 2. Update image and workers
for container in td["containerDefinitions"]:
    if container["name"] == "vyomquant-api":
        container["image"] = "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com/vyomquant-api:95fa5c4265192d106059cd6acab0057cf429e7b7"
        for env_item in container.get("environment", []):
            if env_item["name"] == "WORKERS":
                env_item["value"] = "2"

# 3. Strip metadata fields not accepted by register-task-definition
clean_keys = [
    "family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions",
    "volumes", "placementConstraints", "requiresCompatibilities", "cpu", "memory", "runtimePlatform"
]
new_td = {k: td[k] for k in clean_keys if k in td and td[k] is not None}

with open("new_task_def.json", "w") as f:
    json.dump(new_td, f, indent=2)

print("Registering new task definition with image 95fa5c4 and WORKERS=2...")
reg_res = subprocess.run(
    ["aws", "ecs", "register-task-definition", "--cli-input-json", "file://new_task_def.json", "--region", "ap-southeast-1"],
    capture_output=True, text=True, check=True
)
reg_data = json.loads(reg_res.stdout)
new_rev = reg_data["taskDefinition"]["revision"]
new_arn = reg_data["taskDefinition"]["taskDefinitionArn"]
print(f"Registered Task Definition Revision {new_rev}: {new_arn}")

# 4. Update ECS Service
print("Updating ECS service vyomquant-api-service-cjema2sl...")
up_res = subprocess.run(
    [
        "aws", "ecs", "update-service",
        "--cluster", "vyomquant-cluster",
        "--service", "vyomquant-api-service-cjema2sl",
        "--task-definition", new_arn,
        "--force-new-deployment",
        "--region", "ap-southeast-1"
    ],
    capture_output=True, text=True, check=True
)
print("ECS service update initiated successfully!")
