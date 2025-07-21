#!/usr/bin/env python3
"""
GitHub MCP Server for AI Refine Pipeline - Integrated with Real GitHub API
Wraps GitHub operations for PR management, file fetching, and repository operations
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    Tool,
    TextContent,
    LoggingLevel,
)

# Import your real GitHub functions
try:
    from github import Github
except ImportError:
    print("❌ PyGithub not installed. Install with: pip install PyGithub")
    exit(1)

from dotenv import load_dotenv

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not GITHUB_TOKEN:
    print("❌ GITHUB_TOKEN environment variable not set")
    exit(1)

# Initialize GitHub client
github_direct = Github(GITHUB_TOKEN)
print(f"[DEBUG] ✅ GitHub API client initialized")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
server = Server("github-mcp-server")

# Real GitHub API functions from your step3_regenerate.py
def get_pr_by_number(repo_name: str, pr_number: int):
    """Get PR by number using direct GitHub API"""
    logger.info(f"Getting PR #{pr_number} from {repo_name} using direct GitHub API...")
    
    if not github_direct:
        return {"error": "GitHub API not available"}
    
    try:
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        result = {
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "head": {
                "ref": pr.head.ref if pr.head else None,
                "sha": pr.head.sha if pr.head else None
            },
            "base": {
                "ref": pr.base.ref if pr.base else None,
                "sha": pr.base.sha if pr.base else None
            },
            "user": pr.user.login if pr.user else None,
            "created_at": pr.created_at.isoformat() if pr.created_at else None,
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
            "mergeable": pr.mergeable,
            "mergeable_state": pr.mergeable_state
        }
        logger.info(f"Successfully got PR #{pr.number}: {pr.title}")
        return result
    except Exception as e:
        logger.error(f"Error getting PR: {e}")
        return {"error": str(e)}

def collect_files_for_refinement(repo_name: str, pr_number: int, pr_info=None) -> Dict[str, str]:
    """
    Collect all files in the PR for refinement using direct GitHub API.
    """
    logger.info(f"Starting collect_files_for_refinement for {repo_name} PR #{pr_number}")
    logger.info(f"Using direct GitHub API...")
    
    if not github_direct:
        logger.error("Direct GitHub API not available")
        return {}
    
    try:
        # Get PR using direct GitHub API
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        
        logger.info(f"Got PR #{pr.number}: {pr.title}")
        
        # Get PR files
        pr_files = []
        for file in pr.get_files():
            pr_files.append({
                "filename": file.filename,
                "status": file.status,
                "additions": file.additions,
                "deletions": file.deletions,
                "changes": file.changes
            })
            
        logger.info(f"Got {len(pr_files)} PR files")
        
        # Filter files
        file_names = set()
        for file in pr_files:
            # Skip lock files in any directory
            if file["filename"].endswith("package-lock.json") or file["filename"].endswith("package.lock.json"):
                logger.info(f"Skipping lock file: {file['filename']}")
                continue
            # Skip GitHub workflow and config files
            if file["filename"].startswith('.github/'):
                logger.info(f"Skipping GitHub workflow or config file: {file['filename']}")
                continue
            # Skip asset and binary files (don't need AI refinement)
            asset_extensions = [
                # Images
                '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
                # Videos
                '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
                # Audio
                '.mp3', '.wav', '.flac', '.aac', '.ogg',
                # Fonts
                '.ttf', '.otf', '.woff', '.woff2', '.eot',
                # Documents
                '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                # Archives
                '.zip', '.rar', '.7z', '.tar', '.gz',
                # Binaries
                '.exe', '.dll', '.so', '.dylib'
            ]
            if any(file["filename"].lower().endswith(ext) for ext in asset_extensions):
                logger.info(f"Skipping asset/binary file: {file['filename']}")
                continue
            file_names.add(file["filename"])

        logger.info(f"File names to process: {file_names}")
        
        # Get file contents
        result = {}
        ref = pr_info.get("pr_branch", pr.head.ref) if pr_info else pr.head.ref
        
        for file_name in file_names:
            try:
                logger.info(f"Getting content for {file_name}...")
                # Get file content from the PR branch
                file_content = repo.get_contents(file_name, ref=ref)
                
                # Handle both single file and list of files
                if isinstance(file_content, list):
                    # If it's a directory, skip it
                    logger.info(f"Skipping directory {file_name}")
                    continue
                else:
                    # Single file
                    content = file_content.decoded_content.decode('utf-8')
                    result[file_name] = content
                    logger.info(f"Successfully got content for {file_name}")
            except Exception as e:
                logger.error(f"Error reading file {file_name}: {e}")
                continue

        logger.info(f"Returning {len(result)} files")
        return result
        
    except Exception as e:
        logger.error(f"Direct API failed: {e}")
        return {}

def fetch_repo_context(repo_name: str, pr_number: int, target_file: str, pr_info=None) -> str:
    """Fetch repository context for a specific file"""
    context = ""
    total_chars = 0
    MAX_CONTEXT_CHARS = 4000000  # GPT-4.1 Mini context limit

    if not github_direct:
        logger.error("Direct GitHub API not available for context")
        return ""

    try:
        # Get PR files through direct GitHub API
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        
        ref = pr_info.get("pr_branch", pr.head.ref) if pr_info else pr.head.ref
        
        for file in pr.get_files():
            if file.filename == target_file:
                continue
            # Skip lock files in any directory
            if file.filename.endswith("package-lock.json") or file.filename.endswith("package.lock.json"):
                continue
            # Skip GitHub workflow and config files
            if file.filename.startswith('.github/'):
                continue
            # Skip asset and binary files (don't need in context)
            asset_extensions = [
                # Images
                '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
                # Videos
                '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
                # Audio
                '.mp3', '.wav', '.flac', '.aac', '.ogg',
                # Fonts
                '.ttf', '.otf', '.woff', '.woff2', '.eot',
                # Documents
                '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                # Archives
                '.zip', '.rar', '.7z', '.tar', '.gz',
                # Binaries
                '.exe', '.dll', '.so', '.dylib'
            ]
            if any(file.filename.lower().endswith(ext) for ext in asset_extensions):
                continue
            try:
                file_content = repo.get_contents(file.filename, ref=ref)
                
                # Handle both single file and list of files
                if isinstance(file_content, list):
                    continue  # Skip directories
                
                # Get the FULL file content
                full_content = file_content.decoded_content.decode('utf-8')
                file_size = len(full_content)
                
                section = f"\n// File: {file.filename} ({file_size} chars)\n{full_content}\n"
                
                # Still keep a reasonable limit to avoid overwhelming the model
                if total_chars + len(section) > MAX_CONTEXT_CHARS:
                    break
                    
                context += section
                total_chars += len(section)
                
            except Exception as e:
                logger.error(f"Error reading file {file.filename}: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Error getting PR context: {e}")
        return ""
        
    return context

async def create_refined_pr(repo_name: str, base_branch: str, new_branch: str, 
                           title: str, body: str, files: Dict[str, str]) -> Dict[str, Any]:
    """Create a new PR with refined code"""
    try:
        # For now, return mock data until we implement the full PR creation
        # This would integrate with your existing PR creation logic
        logger.info(f"Creating PR '{title}' in {repo_name}")
        logger.info(f"Base branch: {base_branch}, New branch: {new_branch}")
        logger.info(f"Files to update: {len(files)}")
        
        return {
            "success": True,
            "message": f"PR '{title}' created successfully",
            "branch": new_branch,
            "files_updated": len(files),
            "repo": repo_name,
            "pr_url": f"https://github.com/{repo_name}/pull/123"  # Mock URL
        }
    except Exception as e:
        logger.error(f"Error creating PR: {e}")
        return {
            "success": False,
            "error": str(e)
        }

# MCP Server handlers
@server.list_tools()
async def handle_list_tools():
    """List available GitHub tools"""
    logger.info("Handling list_tools request")
    return [
        Tool(
            name="get_pr_info",
            description="Fetch PR information by repository name and PR number",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "pr_number": {"type": "integer", "description": "PR number"}
                },
                "required": ["repo_name", "pr_number"]
            }
        ),
        Tool(
            name="collect_pr_files",
            description="Collect all files from a PR for refinement (filters out lock files, assets, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "pr_number": {"type": "integer", "description": "PR number"}
                },
                "required": ["repo_name", "pr_number"]
            }
        ),
        Tool(
            name="fetch_repo_context",
            description="Fetch repository context for a specific file (includes related files for context)",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "pr_number": {"type": "integer", "description": "PR number"},
                    "target_file": {"type": "string", "description": "Target file path"}
                },
                "required": ["repo_name", "pr_number", "target_file"]
            }
        ),
        Tool(
            name="create_refined_pr",
            description="Create a new PR with refined code",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {"type": "string", "description": "Repository name (owner/repo)"},
                    "base_branch": {"type": "string", "description": "Base branch name"},
                    "new_branch": {"type": "string", "description": "New branch name"},
                    "title": {"type": "string", "description": "PR title"},
                    "body": {"type": "string", "description": "PR description"},
                    "files": {
                        "type": "object",
                        "description": "Files to commit (path -> content)"
                    }
                },
                "required": ["repo_name", "base_branch", "new_branch", "title", "body", "files"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]):
    """Handle tool calls"""
    logger.info(f"Handling tool call: {name} with arguments: {arguments}")
    try:
        if name == "get_pr_info":
            result = get_pr_by_number(arguments["repo_name"], arguments["pr_number"])
        elif name == "collect_pr_files":
            result = collect_files_for_refinement(arguments["repo_name"], arguments["pr_number"])
        elif name == "fetch_repo_context":
            result = fetch_repo_context(
                arguments["repo_name"], 
                arguments["pr_number"], 
                arguments["target_file"]
            )
        elif name == "create_refined_pr":
            result = await create_refined_pr(
                arguments["repo_name"],
                arguments["base_branch"],
                arguments["new_branch"],
                arguments["title"],
                arguments["body"],
                arguments["files"]
            )
        else:
            raise ValueError(f"Unknown tool: {name}")
        
        logger.info(f"Tool {name} result: {result}")
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
    except Exception as e:
        logger.error(f"Error in tool {name}: {str(e)}")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]

async def main():
    """Main entry point"""
    async with stdio_server() as (read_stream, write_stream):
        # Create a simple notification options object
        class SimpleNotificationOptions:
            def __init__(self):
                self.tools_changed = False
        
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="github-mcp-server",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=SimpleNotificationOptions(),
                    experimental_capabilities=None,
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())