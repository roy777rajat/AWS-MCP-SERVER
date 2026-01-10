from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, Dict, Optional
import boto3
import json
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import os

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


def write_audit_log(action: str, details: Any):
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
    else:
        return obj


# ----------------------
# FASTAPI APP
# ----------------------
app = FastAPI()

# CORS REQUIRED FOR COPILOT STUDIO
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------
# ROOT ENDPOINTS
# ----------------------
@app.get("/")
async def root_get():
    return {"status": "ok", "mcp": "server running"}


@app.post("/")
async def root_post():
    # Copilot Studio expects JSON-RPC error here
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Invalid Request"}
    }


# ----------------------
# MCP TOOL DEFINITIONS
# ----------------------
def get_tools():
    return [
        {
            "name": "list_ec2_instances",
            "description": "List all EC2 instances.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "create_ec2_instance",
            "description": "Create a t2.micro EC2 instance.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "terminate_ec2_instance",
            "description": "Terminate an EC2 instance.",
            "inputSchema": {
                "type": "object",
                "properties": {"instance_id": {"type": "string"}},
                "required": ["instance_id"]
            }
        },
        {
            "name": "list_s3_buckets",
            "description": "List S3 buckets.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "create_s3_bucket",
            "description": "Create an S3 bucket.",
            "inputSchema": {
                "type": "object",
                "properties": {"bucket_name": {"type": "string"}},
                "required": ["bucket_name"]
            }
        },
        {
            "name": "list_lambda_functions",
            "description": "List Lambda functions.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "list_log_groups",
            "description": "List CloudWatch log groups.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "get_estimated_cost",
            "description": "Get AWS cost for last 6 months.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "list_budgets",
            "description": "List AWS budgets.",
            "inputSchema": {
                "type": "object",
                "properties": {"account_id": {"type": "string"}}
            }
        }
    ]


# ----------------------
# MCP /tools/list
# ----------------------

@app.get("/mcp/manifest.json")
async def manifest():
    return {
        "name": "aws-mcp-server",
        "version": "1.0.0",
        "description": "AWS automation tools via MCP",
        "tools": {
            "list": "/mcp/tools/list",
            "call": "/mcp/tools/call"
        }
    }


@app.post("/mcp/tools/list")
async def tools_list(request: Request):
    body = await request.json()
    req_id = body.get("id")
    method = body.get("method")

    if method != "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": "Method not found"}
        }

    tools = get_tools()
    write_audit_log("tools_list", {"count": len(tools)})

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {"tools": tools}
    }


# ----------------------
# MCP /tools/call
# ----------------------
@app.post("/mcp/tools/call")
async def tools_call(request: Request):
    body = await request.json()
    req_id = body.get("id")
    method = body.get("method")

    if method != "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": "Method not found"}
        }

    params = body.get("params", {})
    name = params.get("name")
    args = params.get("arguments", {})

    if not name:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": "Missing tool name"}
        }

    try:
        # ----------------------
        # TOOL DISPATCH
        # ----------------------
        if name == "list_ec2_instances":
            resp = ec2.describe_instances()
            instances = []
            for res in resp["Reservations"]:
                for i in res["Instances"]:
                    instances.append({
                        "instance_id": i["InstanceId"],
                        "state": i["State"]["Name"],
                        "type": i["InstanceType"]
                    })
            result = {"instances": instances}

        elif name == "create_ec2_instance":
            resp = ec2.run_instances(
                ImageId="ami-0fc5d935ebf8bc3bc",
                InstanceType="t2.micro",
                MinCount=1,
                MaxCount=1
            )
            result = {"instance_id": resp["Instances"][0]["InstanceId"]}

        elif name == "terminate_ec2_instance":
            instance_id = args.get("instance_id")
            if not instance_id:
                raise ValueError("instance_id is required")
            ec2.terminate_instances(InstanceIds=[instance_id])
            result = {"terminated_instance_id": instance_id}

        elif name == "list_s3_buckets":
            resp = s3.list_buckets()
            result = {"buckets": [b["Name"] for b in resp["Buckets"]]}

        elif name == "create_s3_bucket":
            bucket = args.get("bucket_name")
            if not bucket:
                raise ValueError("bucket_name is required")
            s3.create_bucket(Bucket=bucket)
            result = {"bucket_created": bucket}

        elif name == "list_lambda_functions":
            resp = lambda_client.list_functions()
            result = {"functions": [f["FunctionName"] for f in resp["Functions"]]}

        elif name == "list_log_groups":
            resp = logs.describe_log_groups()
            result = {"log_groups": [g["logGroupName"] for g in resp["logGroups"]]}

        elif name == "get_estimated_cost":
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

        elif name == "list_budgets":
            account_id = args.get("account_id")
            if not account_id:
                account_id = sts.get_caller_identity()["Account"]
            resp = budgets.describe_budgets(AccountId=account_id)
            result = {"budgets": convert_datetimes(resp.get("Budgets", []))}

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"}
            }

        write_audit_log(name, result)

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": result}
        }

    except Exception as e:
        write_audit_log("error", {"tool": name, "error": str(e)})
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": str(e)}
        }
