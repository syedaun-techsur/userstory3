#!/usr/bin/env python3
"""
GitHub MCP Server - Provides GitHub API functionality through MCP
Fixed version with proper session management and timeout handling
"""
import os
import json
import asyncio
import sys
import logging
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

# Set up logging - only errors and warnings
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

print("=== GitHub MCP Server v2.0 starting ===")
logger.info("GitHub MCP Server v2.0 starting")

load_dotenv()

# Initialize GitHub client
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    logger.error("GITHUB_TOKEN environment variable is required")
    raise ValueError("GITHUB_TOKEN environment variable is required")

try:
    gh = Github(GITHUB_TOKEN)
    # Test the connection
    user = gh.get_user()
    logger.info(f"GitHub API connected successfully as: {user.login}")
except Exception as e:
    logger.error(f"Failed to connect to GitHub API: {e}")
    raise

# Create MCP server with improved configuration
server = Server("github-api-v2")

# GitHub API wrapper functions with better error handling
def safe_github_call(func, *args, **kwargs):
    """Wrapper for GitHub API calls with proper error handling"""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(f"GitHub API error in {func.__name__}: {e}")
        return {"error": str(e)}

def get_user_repos() -> List[Dict[str, Any]]:
    """Get all repositories accessible to the authenticated user"""
    try:
        logger.info("Getting user repositories")
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
        logger.info(f"Retrieved {len(repos)} repositories")
        return repos
    except Exception as e:
        logger.error(f"Error getting repositories: {e}")
        return {"error": str(e)}

def get_pull_requests(repo_name: str, state: str = "open", base: str = "main") -> Union[List[Dict[str, Any]], Dict[str, str]]:
    """Get pull requests for a repository"""
    try:
        logger.info(f"Getting pull requests for {repo_name}")
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
        logger.info(f"Retrieved {len(prs)} pull requests")
        return prs
    except Exception as e:
        logger.error(f"Error getting pull requests: {e}")
        return {"error": str(e)}

def get_pr_by_number(repo_name: str, pr_number: int) -> Dict[str, Any]:
    """Get a specific pull request by number"""
    try:
        logger.info(f"Getting PR #{pr_number} from {repo_name}")
        repo = gh.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        result = {
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
        logger.info(f"Retrieved PR #{pr.number}: {pr.title}")
        return result
    except Exception as e:
        logger.error(f"Error getting PR #{pr_number}: {e}")
        return {"error": str(e)}

def get_pr_files(repo_name: str, pr_number: int) -> Union[List[Dict[str, Any]], Dict[str, str]]:
    """Get files changed in a pull request"""
    try:
        logger.info(f"Getting files for PR #{pr_number} from {repo_name}")
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
        logger.info(f"Retrieved {len(files)} files from PR #{pr_number}")
        return files
    except Exception as e:
        logger.error(f"Error getting PR files: {e}")
        return {"error": str(e)}

def get_pr_comments(repo_name: str, pr_number: int) -> Union[List[Dict[str, Any]], Dict[str, str]]:
    """Get issue comments for a pull request"""
    try:
        logger.info(f"Getting comments for PR #{pr_number}")
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
        logger.info(f"Retrieved {len(comments)} comments")
        return comments
    except Exception as e:
        logger.error(f"Error getting PR comments: {e}")
        return {"error": str(e)}

def get_pr_review_comments(repo_name: str, pr_number: int) -> Union[List[Dict[str, Any]], Dict[str, str]]:
    """Get review comments for a pull request"""
    try:
        logger.info(f"Getting review comments for PR #{pr_number}")
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
        logger.info(f"Retrieved {len(comments)} review comments")
        return comments
    except Exception as e:
        logger.error(f"Error getting PR review comments: {e}")
        return {"error": str(e)}

def get_file_content(repo_name: str, file_path: str, ref: str = "main") -> Dict[str, Any]:
    """Get content of a file from a repository"""
    try:
        logger.info(f"Getting content for {file_path} from {repo_name}")
        repo = gh.get_repo(repo_name)
        content = repo.get_contents(file_path, ref=ref)
        # Handle both single file and list of files
        if isinstance(content, list):
            content = content[0]  # Take the first file if it's a list
        
        decoded_content = content.decoded_content.decode("utf-8")
        result = {
            "path": content.path,
            "content": decoded_content,
            "sha": content.sha,
            "size": content.size,
            "type": content.type
        }
        logger.info(f"Retrieved content for {file_path} ({len(decoded_content)} chars)")
        return result
    except Exception as e:
        error_msg = str(e)
        # Don't log 404 errors as errors since they're expected for new files
        if "404" in error_msg or "Not Found" in error_msg:
            logger.debug(f"File not found: {file_path} (expected for new files)")
        else:
            logger.error(f"Error getting file content: {e}")
        return {"error": error_msg}

def create_branch(repo_name: str, branch_name: str, base_sha: str) -> Dict[str, Any]:
    """Create a new branch in a repository"""
    try:
        logger.info(f"Creating branch {branch_name} in {repo_name}")
        repo = gh.get_repo(repo_name)
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=base_sha)
        result = {
            "success": True,
            "branch": branch_name,
            "sha": base_sha
        }
        logger.info(f"Created branch {branch_name}")
        return result
    except Exception as e:
        logger.error(f"Error creating branch: {e}")
        return {"error": str(e)}

def update_file(repo_name: str, file_path: str, message: str, content: str, sha: str, branch: str) -> Dict[str, Any]:
    """Update a file in a repository"""
    try:
        logger.info(f"Updating file {file_path} in {repo_name}")
        repo = gh.get_repo(repo_name)
        
        # Handle large files specially
        if len(content) > 1000000:  # 1MB
            logger.warning(f"Large file detected: {file_path} ({len(content)} bytes)")
        
        result = repo.update_file(
            path=file_path,
            message=message,
            content=content,
            sha=sha,
            branch=branch
        )
        
        # Handle case where commit might be None
        commit_info = {}
        if result.get("commit"):
            commit_info["sha"] = getattr(result["commit"], 'sha', None)
            if hasattr(result["commit"], 'commit') and result["commit"].commit:
                commit_info["message"] = getattr(result["commit"].commit, 'message', message)
            else:
                commit_info["message"] = message
        
        response = {
            "success": True,
            "commit": commit_info,
            "content": {
                "path": file_path,
                "sha": getattr(result.get("content"), 'sha', None) if result.get("content") else None
            }
        }
        logger.info(f"Updated file {file_path}")
        return response
    except Exception as e:
        logger.error(f"Error updating file {file_path}: {e}")
        return {"error": str(e)}

def create_file(repo_name: str, file_path: str, message: str, content: str, branch: str) -> Dict[str, Any]:
    """Create a new file in a repository"""
    try:
        logger.info(f"Creating file {file_path} in {repo_name}")
        repo = gh.get_repo(repo_name)
        
        # Handle large files specially
        if len(content) > 1000000:  # 1MB
            logger.warning(f"Large file detected: {file_path} ({len(content)} bytes)")
        
        result = repo.create_file(
            path=file_path,
            message=message,
            content=content,
            branch=branch
        )
        
        # Handle case where commit might be None
        commit_info = {}
        if result.get("commit"):
            commit_info["sha"] = getattr(result["commit"], 'sha', None)
            if hasattr(result["commit"], 'commit') and result["commit"].commit:
                commit_info["message"] = getattr(result["commit"].commit, 'message', message)
            else:
                commit_info["message"] = message
        
        response = {
            "success": True,
            "commit": commit_info,
            "content": {
                "path": file_path,
                "sha": getattr(result.get("content"), 'sha', None) if result.get("content") else None
            }
        }
        logger.info(f"Created file {file_path}")
        return response
    except Exception as e:
        logger.error(f"Error creating file {file_path}: {e}")
        return {"error": str(e)}

def create_pull_request(repo_name: str, title: str, body: str, head: str, base: str) -> Dict[str, Any]:
    """Create a new pull request"""
    try:
        logger.info(f"Creating PR in {repo_name}: {title}")
        repo = gh.get_repo(repo_name)
        pr = repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=base
        )
        result = {
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
        logger.info(f"Created PR #{pr.number}")
        return result
    except Exception as e:
        logger.error(f"Error creating PR: {e}")
        return {"error": str(e)}

def get_branch(repo_name: str, branch_name: str) -> Dict[str, Any]:
    """Get information about a branch"""
    try:
        logger.info(f"Getting branch {branch_name} from {repo_name}")
        repo = gh.get_repo(repo_name)
        branch = repo.get_branch(branch_name)
        
        # Handle case where commit might be None
        commit_info = {}
        if branch.commit:
            commit_info["sha"] = getattr(branch.commit, 'sha', None)
            if hasattr(branch.commit, 'commit') and branch.commit.commit:
                commit_info["message"] = getattr(branch.commit.commit, 'message', '')
            else:
                commit_info["message"] = ''
        
        result = {
            "name": branch.name,
            "commit": commit_info,
            "protected": getattr(branch, 'protected', False)
        }
        logger.info(f"Retrieved branch {branch_name}")
        return result
    except Exception as e:
        logger.error(f"Error getting branch: {e}")
        return {"error": str(e)}

def check_branch_exists(repo_name: str, branch_name: str) -> Dict[str, Any]:
    """Check if a branch exists in a repository"""
    try:
        logger.info(f"Checking if branch {branch_name} exists in {repo_name}")
        repo = gh.get_repo(repo_name)
        branches = [b.name for b in repo.get_branches()]
        exists = branch_name in branches
        result = {
            "exists": exists,
            "branch": branch_name,
            "available_branches": branches
        }
        logger.info(f"Branch {branch_name} exists: {exists}")
        return result
    except Exception as e:
        logger.error(f"Error checking branch existence: {e}")
        return {"error": str(e)}

# MCP Server handlers with improved error handling
@server.list_tools()
async def handle_list_tools() -> ListToolsResult:
    """List available tools"""
    logger.info("Listing available tools")
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "state": {"type": "string", "default": "open", "description": "PR state (open, closed, all)"},
                    "base": {"type": "string", "default": "main", "description": "Base branch name"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "pr_number": {"type": "integer", "description": "Pull request number"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "pr_number": {"type": "integer", "description": "Pull request number"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "pr_number": {"type": "integer", "description": "Pull request number"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "pr_number": {"type": "integer", "description": "Pull request number"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "file_path": {"type": "string", "description": "Path to the file"},
                    "ref": {"type": "string", "default": "main", "description": "Branch or commit reference"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "branch_name": {"type": "string", "description": "Name of the new branch"},
                    "base_sha": {"type": "string", "description": "SHA of the commit to branch from"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "file_path": {"type": "string", "description": "Path to the file"},
                    "message": {"type": "string", "description": "Commit message"},
                    "content": {"type": "string", "description": "New file content"},
                    "sha": {"type": "string", "description": "SHA of the current file"},
                    "branch": {"type": "string", "description": "Branch to update"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "file_path": {"type": "string", "description": "Path for the new file"},
                    "message": {"type": "string", "description": "Commit message"},
                    "content": {"type": "string", "description": "File content"},
                    "branch": {"type": "string", "description": "Branch to create file in"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "title": {"type": "string", "description": "PR title"},
                    "body": {"type": "string", "description": "PR description"},
                    "head": {"type": "string", "description": "Head branch"},
                    "base": {"type": "string", "description": "Base branch"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "branch_name": {"type": "string", "description": "Branch name"}
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
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "branch_name": {"type": "string", "description": "Branch name to check"}
                },
                "required": ["repo_name", "branch_name"]
            }
        )
    ]
    logger.info(f"Listed {len(tools)} available tools")
    return ListToolsResult(tools=tools)

@server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls with improved error handling and logging"""
    logger.info(f"Tool called: {name} with arguments: {arguments}")
    
    try:
        # Route to appropriate function
        if name == "get_user_repos":
            result = get_user_repos()
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
            logger.error(f"Unknown tool: {name}")
            result = {"error": f"Unknown tool: {name}"}
        
        # Convert result to JSON string
        json_result = json.dumps(result)
        logger.info(f"Tool {name} completed successfully")
        
        return [TextContent(type="text", text=json_result)]
        
    except Exception as e:
        logger.error(f"Exception in tool {name}: {e}", exc_info=True)
        error_result = {"error": f"Tool execution error: {str(e)}"}
        return [TextContent(type="text", text=json.dumps(error_result))]

async def main():
    """Main server function with improved error handling"""
    try:
        logger.info("Starting GitHub MCP Server v2.0")
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, 
                write_stream, 
                server.create_initialization_options()
            )
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1) 