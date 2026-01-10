from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import boto3
import json
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import os

# ----------------------
# AWS config
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
    """Write simple audit logs to S3."""
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
        pass  # Do not break tool execution if logging fails


def convert_datetimes(obj):
    """Recursively convert datetimes to ISO strings."""
    if isinstance(obj, dict):
        return {k: convert_datetimes(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_datetimes(i) for i in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    else:
        return obj


# ---------------
# MCP models
# ---------------
class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Any] = None
    method: str
    params: Optional[Dict[str, Any]] = {}


class MCPToolCallParams(BaseModel):
    name: str
    arguments: Dict[str, Any]


# ----------------------
# FastAPI app
# ----------------------
app = FastAPI()

# ----------------------
# CORS (required for Copilot Studio)
# ----------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------
# Root health check (required by Copilot Studio)
# ----------------------
@app.get("/")
async def root():
    return {"status": "ok", "mcp": "server running"}


# ---------------
# MCP tools definition
# ---------------
def get_mcp_tools_definition() -> List[Dict[str, Any]]:
    return [
        {
            "name": "list_ec2_instances",
            "description": "List all EC2 instances in the configured region.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "create_ec2_instance",
            "description": "Create a t2.micro EC2 instance with a default AMI.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "terminate_ec2_instance",
            "description": "Terminate an EC2 instance by instance_id.",
            "inputSchema": {
                "type": "object",
                "properties": {"instance_id": {"type": "string"}},
                "required": ["instance_id"]
            }
        },
        {
            "name": "list_s3_buckets",
            "description": "List all S3 buckets.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "create_s3_bucket",
            "description": "Create an S3 bucket with the given name.",
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
            "description": "Get monthly AWS cost for the last 6 months.",
            "inputSchema": {"type": "object", "properties": {}}
        },
        {
            "name": "list_budgets",
            "description": "List AWS budgets for the current account.",
            "inputSchema": {
                "type": "object",
                "properties": {"account_id": {"type": "string"}}
            }
        }
    ]


# ---------------
# MCP endpoint: tools/list (POST)
# ---------------
@app.post("/mcp/tools/list")
async def mcp_tools_list(req: MCPRequest):
    tools = get_mcp_tools_definition()
    write_audit_log("mcp_tools_list", {"tool_count": len(tools)})
    return {
        "jsonrpc": "2.0",
        "id": req.id,
        "result": {"tools": tools}
    }


# ---------------
# MCP endpoint: tools/list (GET) — required for Copilot Studio
# ---------------
@app.get("/mcp/tools/list")
async def mcp_tools_list_get():
    tools = get_mcp_tools_definition()
    return {
        "jsonrpc": "2.0",
        "id": None,
        "result": {"tools": tools}
    }


# ---------------
# MCP endpoint: tools/call (POST only)
# ---------------
@app.post("/mcp/tools/call")
async def mcp_tools_call(req: MCPRequest):
    if not req.params:
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "error": {"code": -32602, "message": "Missing params"}
        }

    try:
        params = MCPToolCallParams(**req.params)
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "error": {"code": -32602, "message": f"Invalid params: {str(e)}"}
        }

    tool_name = params.name
    args = params.arguments or {}

    try:
        # ----------------------
        # EC2
        # ----------------------
        if tool_name == "list_ec2_instances":
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

        elif tool_name == "create_ec2_instance":
            resp = ec2.run_instances(
                ImageId="ami-0fc5d935ebf8bc3bc",
                InstanceType="t2.micro",
                MinCount=1,
                MaxCount=1
            )
            instance_id = resp["Instances"][0]["InstanceId"]
            result = {"instance_id": instance_id}

        elif tool_name == "terminate_ec2_instance":
            instance_id = args.get("instance_id")
            if not instance_id:
                raise ValueError("instance_id is required")
            ec2.terminate_instances(InstanceIds=[instance_id])
            result = {"terminated_instance_id": instance_id}

        # ----------------------
        # S3
        # ----------------------
        elif tool_name == "list_s3_buckets":
            resp = s3.list_buckets()
            result = {"buckets": [b["Name"] for b in resp["Buckets"]]}

        elif tool_name == "create_s3_bucket":
            bucket = args.get("bucket_name")
            if not bucket:
                raise ValueError("bucket_name is required")
            s3.create_bucket(Bucket=bucket)
            result = {"bucket_created": bucket}

        # ----------------------
        # Lambda
        # ----------------------
        elif tool_name == "list_lambda_functions":
            resp = lambda_client.list_functions()
            result = {"functions": [f["FunctionName"] for f in resp["Functions"]]}

        # ----------------------
        # CloudWatch
        # ----------------------
        elif tool_name == "list_log_groups":
            resp = logs.describe_log_groups()
            result = {"log_groups": [g["logGroupName"] for g in resp["logGroups"]]}

        # ----------------------
        # Cost Explorer
        # ----------------------
        elif tool_name == "get_estimated_cost":
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

        # ----------------------
        # Budgets
        # ----------------------
        elif tool_name == "list_budgets":
            account_id = args.get("account_id")
            if not account_id:
                account_id = sts.get_caller_identity()["Account"]
            resp = budgets.describe_budgets(AccountId=account_id)
            result = {"budgets": convert_datetimes(resp.get("Budgets", []))}

        else:
            return {
                "jsonrpc": "2.0",
                "id": req.id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            }

        write_audit_log(tool_name, result)

        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "result": {"content": result}
        }

    except Exception as e:
        write_audit_log("error", {"tool": tool_name, "error": str(e)})
        return {
            "jsonrpc": "2.0",
            "id": req.id,
            "error": {"code": -32000, "message": str(e)}
        }
