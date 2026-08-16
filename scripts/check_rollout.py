import subprocess
import json

def check_deployment():
    print("Checking ECS Service Deployments...")
    res = subprocess.run(
        ["aws", "ecs", "describe-services", "--cluster", "vyomquant-cluster", "--services", "vyomquant-api-service-cjema2sl", "--region", "ap-southeast-1"],
        capture_output=True, text=True, check=True
    )
    data = json.loads(res.stdout)
    srv = data["services"][0]
    for d in srv.get("deployments", []):
        print(f"Deployment: {d['id']} | Status: {d['status']} | TaskDef: {d['taskDefinition'].split('/')[-1]} | Desired: {d['desiredCount']} | Running: {d['runningCount']} | Pending: {d['pendingCount']}")

    print("\nChecking Target Health...")
    tg_res = subprocess.run(
        ["aws", "elbv2", "describe-target-health", "--target-group-arn", "arn:aws:elasticloadbalancing:ap-southeast-1:273709947018:targetgroup/vyomquant-api-tg/9e93b22862870f31", "--region", "ap-southeast-1"],
        capture_output=True, text=True, check=True
    )
    tg_data = json.loads(tg_res.stdout)
    for th in tg_data.get("TargetHealthDescriptions", []):
        print(f"Target: {th['Target']['Id']}:{th['Target']['Port']} | State: {th['TargetHealth']['State']}")

check_deployment()
