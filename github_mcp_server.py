#!/usr/bin/env python3
"""
GitHub MCP Server - Provides GitHub API functionality through MCP
Replaces PyGithub with MCP tools for consistent architecture
"""
print("=== GitHub MCP Server starting ===")
import os
import json
import asyncio
from typing import List, Dict, Any, Optional, Union
from github import Github
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    Tool,
    TextContent
)
from dotenv import load_dotenv

load_dotenv()

# Initialize GitHub client
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN environment variable is required")

gh = Github(GITHUB_TOKEN)

# Create MCP server
server = Server("GitHub API")

# GitHub API functions
def get_user_repos() -> List[Dict[str, Any]]:
    """Get all repositories accessible to the authenticated user"""
    repos = []
    for repo in gh.get_user().get_repos():
        repos.append({
            "full_name": repo.full_name,
            "name": repo.name,
            "owner": repo.owner.login,
            "private": repo.private,
            "description": repo.description,
            "default_branch": repo.default_branch
        })
    return repos

def get_pull_requests(repo_name: str, state: str = "open", base: str = "main") -> Union[List[Dict[str, Any]], Dict[str, str]]:
    """Get pull requests for a repository"""
    try:
        repo = gh.get_repo(repo_name)
        prs = []
        for pr in repo.get_pulls(state=state, base=base):
            pr_data = {
                "number": pr.number,
                "title": pr.title,
                "state": pr.state,
                "head": {
                    "ref": pr.head.ref,
                    "sha": pr.head.sha
                },
                "base": {
                    "ref": pr.base.ref,
                    "sha": pr.base.sha
                },
                "user": pr.user.login,
                "created_at": pr.created_at.isoformat() if pr.created_at else None,
                "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
                "mergeable": pr.mergeable,
                "mergeable_state": pr.mergeable_state
            }
            prs.append(pr_data)
        return prs
    except Exception as e:
        return {"error": str(e)}

def get_pr_by_number(repo_name: str, pr_number: int) -> Dict[str, Any]:
    """Get a specific pull request by number"""
    try:
        repo = gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        return {
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "head": {
                "ref": pr.head.ref,
                "sha": pr.head.sha
            },
            "base": {
                "ref": pr.base.ref,
                "sha": pr.base.sha
            },
            "user": pr.user.login,
            "created_at": pr.created_at.isoformat() if pr.created_at else None,
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
            "mergeable": pr.mergeable,
            "mergeable_state": pr.mergeable_state
        }
    except Exception as e:
        return {"error": str(e)}

def get_pr_files(repo_name: str, pr_number: int) -> Union[List[Dict[str, Any]], Dict[str, str]]:
    """Get files changed in a pull request"""
    try:
        repo = gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        files = []
        for file in pr.get_files():
            files.append({
                "filename": file.filename,
                "status": file.status,
                "additions": file.additions,
                "deletions": file.deletions,
                "changes": file.changes,
                "patch": file.patch
            })
        return files
    except Exception as e:
        return {"error": str(e)}

def get_pr_comments(repo_name: str, pr_number: int) -> Union[List[Dict[str, Any]], Dict[str, str]]:
    """Get issue comments for a pull request"""
    try:
        repo = gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        comments = []
        for comment in pr.get_issue_comments():
            comments.append({
                "id": comment.id,
                "body": comment.body,
                "user": comment.user.login,
                "created_at": comment.created_at.isoformat() if comment.created_at else None,
                "updated_at": comment.updated_at.isoformat() if comment.updated_at else None
            })
        return comments
    except Exception as e:
        return {"error": str(e)}

def get_pr_review_comments(repo_name: str, pr_number: int) -> Union[List[Dict[str, Any]], Dict[str, str]]:
    """Get review comments for a pull request"""
    try:
        repo = gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        comments = []
        for comment in pr.get_review_comments():
            comments.append({
                "id": comment.id,
                "body": comment.body,
                "path": comment.path,
                "line": comment.line,
                "user": comment.user.login,
                "created_at": comment.created_at.isoformat() if comment.created_at else None,
                "updated_at": comment.updated_at.isoformat() if comment.updated_at else None
            })
        return comments
    except Exception as e:
        return {"error": str(e)}

def get_file_content(repo_name: str, file_path: str, ref: str = "main") -> Dict[str, Any]:
    """Get content of a file from a repository"""
    try:
        repo = gh.get_repo(repo_name)
        content = repo.get_contents(file_path, ref=ref)
        # Handle both single file and list of files
        if isinstance(content, list):
            content = content[0]  # Take the first file if it's a list
        return {
            "path": content.path,
            "content": content.decoded_content.decode("utf-8"),
            "sha": content.sha,
            "size": content.size,
            "type": content.type
        }
    except Exception as e:
        return {"error": str(e)}

def create_branch(repo_name: str, branch_name: str, base_sha: str) -> Dict[str, Any]:
    """Create a new branch in a repository"""
    try:
        repo = gh.get_repo(repo_name)
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)
        return {
            "success": True,
            "branch": branch_name,
            "sha": base_sha
        }
    except Exception as e:
        return {"error": str(e)}

def update_file(repo_name: str, file_path: str, message: str, content: str, sha: str, branch: str) -> Dict[str, Any]:
    """Update a file in a repository"""
    try:
        repo = gh.get_repo(repo_name)
        result = repo.update_file(
            path=file_path,
            message=message,
            content=content,
            sha=sha,
            branch=branch
        )
        return {
            "success": True,
            "commit": {
                "sha": result["commit"].sha,
                "message": result["commit"].commit.message
            },
            "content": {
                "path": file_path,
                "sha": getattr(result["content"], 'sha', None)
            }
        }
    except Exception as e:
        return {"error": str(e)}

def create_file(repo_name: str, file_path: str, message: str, content: str, branch: str) -> Dict[str, Any]:
    """Create a new file in a repository"""
    try:
        repo = gh.get_repo(repo_name)
        result = repo.create_file(
            path=file_path,
            message=message,
            content=content,
            branch=branch
        )
        return {
            "success": True,
            "commit": {
                "sha": result["commit"].sha,
                "message": result["commit"].commit.message
            },
            "content": {
                "path": file_path,
                "sha": getattr(result["content"], 'sha', None)
            }
        }
    except Exception as e:
        return {"error": str(e)}

def create_pull_request(repo_name: str, title: str, body: str, head: str, base: str) -> Dict[str, Any]:
    """Create a new pull request"""
    try:
        repo = gh.get_repo(repo_name)
        pr = repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=base
        )
        return {
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "head": {
                "ref": pr.head.ref,
                "sha": pr.head.sha
            },
            "base": {
                "ref": pr.base.ref,
                "sha": pr.base.sha
            },
            "user": pr.user.login
        }
    except Exception as e:
        return {"error": str(e)}

def get_branch(repo_name: str, branch_name: str) -> Dict[str, Any]:
    """Get information about a branch"""
    try:
        repo = gh.get_repo(repo_name)
        branch = repo.get_branch(branch_name)
        return {
            "name": branch.name,
            "commit": {
                "sha": branch.commit.sha,
                "message": branch.commit.commit.message
            },
            "protected": branch.protected
        }
    except Exception as e:
        return {"error": str(e)}

def check_branch_exists(repo_name: str, branch_name: str) -> Dict[str, Any]:
    """Check if a branch exists in a repository"""
    try:
        repo = gh.get_repo(repo_name)
        branches = [b.name for b in repo.get_branches()]
        return {
            "exists": branch_name in branches,
            "branch": branch_name,
            "available_branches": branches
        }
    except Exception as e:
        return {"error": str(e)}

# MCP Server handlers
@server.list_tools()
async def handle_list_tools():
    """List available tools"""
    tools = [
        Tool(
            name="get_user_repos",
            description="Get all repositories accessible to the authenticated user",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="get_pull_requests",
            description="Get pull requests for a repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "state": {"type": "string", "default": "open"},
                    "base": {"type": "string", "default": "main"}
                },
                "required": ["repo_name"]
            }
        ),
        Tool(
            name="get_pr_by_number",
            description="Get a specific pull request by number",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "pr_number": {"type": "integer"}
                },
                "required": ["repo_name", "pr_number"]
            }
        ),
        Tool(
            name="get_pr_files",
            description="Get files changed in a pull request",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "pr_number": {"type": "integer"}
                },
                "required": ["repo_name", "pr_number"]
            }
        ),
        Tool(
            name="get_pr_comments",
            description="Get issue comments for a pull request",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "pr_number": {"type": "integer"}
                },
                "required": ["repo_name", "pr_number"]
            }
        ),
        Tool(
            name="get_pr_review_comments",
            description="Get review comments for a pull request",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "pr_number": {"type": "integer"}
                },
                "required": ["repo_name", "pr_number"]
            }
        ),
        Tool(
            name="get_file_content",
            description="Get content of a file from a repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "file_path": {"type": "string"},
                    "ref": {"type": "string", "default": "main"}
                },
                "required": ["repo_name", "file_path"]
            }
        ),
        Tool(
            name="create_branch",
            description="Create a new branch in a repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "branch_name": {"type": "string"},
                    "base_sha": {"type": "string"}
                },
                "required": ["repo_name", "branch_name", "base_sha"]
            }
        ),
        Tool(
            name="update_file",
            description="Update a file in a repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "file_path": {"type": "string"},
                    "message": {"type": "string"},
                    "content": {"type": "string"},
                    "sha": {"type": "string"},
                    "branch": {"type": "string"}
                },
                "required": ["repo_name", "file_path", "message", "content", "sha", "branch"]
            }
        ),
        Tool(
            name="create_file",
            description="Create a new file in a repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "file_path": {"type": "string"},
                    "message": {"type": "string"},
                    "content": {"type": "string"},
                    "branch": {"type": "string"}
                },
                "required": ["repo_name", "file_path", "message", "content", "branch"]
            }
        ),
        Tool(
            name="create_pull_request",
            description="Create a new pull request",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "head": {"type": "string"},
                    "base": {"type": "string"}
                },
                "required": ["repo_name", "title", "body", "head", "base"]
            }
        ),
        Tool(
            name="get_branch",
            description="Get information about a branch",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "branch_name": {"type": "string"}
                },
                "required": ["repo_name", "branch_name"]
            }
        ),
        Tool(
            name="check_branch_exists",
            description="Check if a branch exists in a repository",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string"},
                    "branch_name": {"type": "string"}
                },
                "required": ["repo_name", "branch_name"]
            }
        )
    ]
    return tools

@server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]):
    """Handle tool calls"""
    print(f"[DEBUG] Tool called: {name} with arguments: {arguments}")
    try:
        if name == "get_user_repos":
            print("[DEBUG] Calling get_user_repos()")
            result = get_user_repos()
            print(f"[DEBUG] get_user_repos result: {result}")
        elif name == "get_pull_requests":
            result = get_pull_requests(**arguments)
        elif name == "get_pr_by_number":
            result = get_pr_by_number(**arguments)
        elif name == "get_pr_files":
            result = get_pr_files(**arguments)
        elif name == "get_pr_comments":
            result = get_pr_comments(**arguments)
        elif name == "get_pr_review_comments":
            result = get_pr_review_comments(**arguments)
        elif name == "get_file_content":
            result = get_file_content(**arguments)
        elif name == "create_branch":
            result = create_branch(**arguments)
        elif name == "update_file":
            result = update_file(**arguments)
        elif name == "create_file":
            result = create_file(**arguments)
        elif name == "create_pull_request":
            result = create_pull_request(**arguments)
        elif name == "get_branch":
            result = get_branch(**arguments)
        elif name == "check_branch_exists":
            result = check_branch_exists(**arguments)
        else:
            print(f"[DEBUG] Unknown tool: {name}")
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]
        
        print(f"[DEBUG] Returning result: {result}")
        return [TextContent(type="text", text=json.dumps(result))]
    except Exception as e:
        print(f"[DEBUG] Exception in tool handler: {e}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

if __name__ == "__main__":
    import asyncio

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main()) 