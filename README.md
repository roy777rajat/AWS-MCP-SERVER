# AWS MCP Server

This is a Python FastAPI-based MCP server that exposes AWS operations as MCP tools.

## Features

- EC2:
  - List EC2 instances
  - Create t2.micro EC2 instance
  - Terminate EC2 instance
- S3:
  - List buckets
  - Create bucket
- Lambda:
  - List functions
- CloudWatch:
  - List log groups
- Cost Explorer:
  - Get estimated costs for the last 6 months
- Budgets:
  - List budgets

## Local Development

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_REGION=eu-west-1
export AUDIT_BUCKET=aws-mcp-audit-logs-rajat

uvicorn server:app --host 0.0.0.0 --port 8000
