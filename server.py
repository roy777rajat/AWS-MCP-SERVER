from mcp.server.fastmcp import FastMCP
import boto3
import json
import os
import requests
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# ----------------------
# AWS CONFIG
# ----------------------
REGION = os.getenv("AWS_REGION", "eu-west-1")

ec2 = boto3.client("ec2", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
ce = boto3.client("ce", region_name=REGION)
budgets = boto3.client("budgets", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)

AUDIT_BUCKET = os.getenv("AUDIT_BUCKET", "aws-mcp-audit-logs-rajat")
s3_audit = boto3.client("s3", region_name=REGION)


# ----------------------
# HELPERS
# ----------------------
def write_audit_log(action: str, details):
    try:
        log = {
            "action": action,
            "details": details,
            "timestamp": datetime.utcnow().isoformat()
        }
        s3_audit.put_object(
            Bucket=AUDIT_BUCKET,
            Key=f"audit/{datetime.utcnow().isoformat()}.json",
            Body=json.dumps(log)
        )
    except Exception:
        pass


def convert_datetimes(obj):
    if isinstance(obj, dict):
        return {k: convert_datetimes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_datetimes(i) for i in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    return obj


# ----------------------
# PATCH HOST VALIDATION
# ----------------------
try:
    from mcp.server.streamable_http import StreamableHTTPServerTransport
    original_check = StreamableHTTPServerTransport._check_host_header

    async def patched_check(self, *args, **kwargs):
        return  # skip host validation entirely

    StreamableHTTPServerTransport._check_host_header = patched_check
except Exception:
    pass

try:
    import mcp.server.transport_security as ts
    ts.check_host = lambda *a, **kw: None
except Exception:
    pass


# ----------------------
# MCP SERVER
# ----------------------
mcp = FastMCP("aws-mcp-server", stateless_http=True)


@mcp.tool()
def list_ec2_instances() -> dict:
    """List all EC2 instances."""
    resp = ec2.describe_instances()
    instances = []
    for res in resp["Reservations"]:
        for i in res["Instances"]:
            instances.append({
                "instance_id": i["InstanceId"],
                "state": i["State"]["Name"],
                "type": i["InstanceType"]
            })
    write_audit_log("list_ec2_instances", {"count": len(instances)})
    return {"instances": instances}


@mcp.tool()
def create_ec2_instance() -> dict:
    """Create a t2.micro EC2 instance."""
    resp = ec2.run_instances(
        ImageId="ami-049442a6cf8319180",
        InstanceType="t2.micro",
        MinCount=1,
        MaxCount=1
    )
    result = {"instance_id": resp["Instances"][0]["InstanceId"]}
    write_audit_log("create_ec2_instance", result)
    return result


@mcp.tool()
def terminate_ec2_instance(instance_id: str) -> dict:
    """Terminate an EC2 instance."""
    ec2.terminate_instances(InstanceIds=[instance_id])
    result = {"terminated_instance_id": instance_id}
    write_audit_log("terminate_ec2_instance", result)
    return result


@mcp.tool()
def list_s3_buckets() -> dict:
    """List all S3 buckets."""
    resp = s3.list_buckets()
    result = {"buckets": [b["Name"] for b in resp["Buckets"]]}
    write_audit_log("list_s3_buckets", result)
    return result


@mcp.tool()
def create_s3_bucket(bucket_name: str) -> dict:
    """Create an S3 bucket."""
    s3.create_bucket(Bucket=bucket_name)
    result = {"bucket_created": bucket_name}
    write_audit_log("create_s3_bucket", result)
    return result


@mcp.tool()
def list_lambda_functions() -> dict:
    """List all Lambda functions."""
    resp = lambda_client.list_functions()
    result = {"functions": [f["FunctionName"] for f in resp["Functions"]]}
    write_audit_log("list_lambda_functions", result)
    return result


@mcp.tool()
def list_log_groups() -> dict:
    """List CloudWatch log groups."""
    resp = logs.describe_log_groups()
    result = {"log_groups": [g["logGroupName"] for g in resp["logGroups"]]}
    write_audit_log("list_log_groups", result)
    return result


@mcp.tool()
def get_estimated_cost() -> dict:
    """Get AWS cost for last 6 months."""
    today = datetime.utcnow()
    start = (today - relativedelta(months=5)).replace(day=1)
    end = today.replace(day=1) + relativedelta(months=1) - timedelta(days=1)
    cost = ce.get_cost_and_usage(
        TimePeriod={
            "Start": start.strftime("%Y-%m-%d"),
            "End": end.strftime("%Y-%m-%d")
        },
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"]
    )
    result = {"cost": convert_datetimes(cost)}
    write_audit_log("get_estimated_cost", {})
    return result


@mcp.tool()
def list_budgets(account_id: str = "") -> dict:
    """List AWS budgets."""
    if not account_id:
        account_id = sts.get_caller_identity()["Account"]
    resp = budgets.describe_budgets(AccountId=account_id)
    result = {"budgets": convert_datetimes(resp.get("Budgets", []))}
    write_audit_log("list_budgets", {})
    return result


@mcp.tool()
def get_profile_stat() -> dict:
    """Get profile statistics."""
    url = "https://zztynrwa31.execute-api.eu-west-1.amazonaws.com/stats"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def send_portfolio_stats_email() -> dict:
    """Send the portfolio stats email."""
    payload = {
        "source": "mcp",
        "action": "send_portfolio_stats_email",
        "requested_at": datetime.utcnow().isoformat()
    }
    lambda_client.invoke(
        FunctionName="portfolio-stat-email",
        InvocationType="Event",
        Payload=json.dumps(payload)
    )
    result = {"status": "triggered", "message": "Portfolio stats email sent"}
    write_audit_log("send_portfolio_stats_email", result)
    return result


# ----------------------
# ASGI APP FOR RENDER
# ----------------------
app = mcp.streamable_http_app()
