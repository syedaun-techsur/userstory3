#!/usr/bin/env python3
"""
GitHub MCP Client - Provides a clean interface to GitHub MCP server
Replaces PyGithub with MCP-based GitHub interactions
"""

import asyncio
import json
from typing import List, Dict, Any, Optional
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp import StdioServerParameters

class GitHubMCPClient:
    """Client for interacting with GitHub through MCP"""
    
    def __init__(self):
        self.server_params = StdioServerParameters(
            command="python", 
            args=["github_mcp_server.py"]
        )
    
    async def _call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call a tool on the GitHub MCP server"""
        async with stdio_client(self.server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments),
                    timeout=30  # 30 second timeout
                )
                # MCP returns content as a list of TextContent objects
                if result.content and len(result.content) > 0:
                    content_item = result.content[0]
                    # Check if it's a TextContent object
                    if hasattr(content_item, 'text') and hasattr(content_item, 'type') and content_item.type == "text":
                        # Parse the text content as JSON
                        return json.loads(content_item.text)
                    else:
                        return {"error": f"Unexpected content type: {type(content_item)}"}
                else:
                    return {"error": "No response from MCP server"}
    
    def call_tool_sync(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Synchronous wrapper for tool calls"""
        try:
            return asyncio.run(self._call_tool(tool_name, arguments))
        except Exception as e:
            return {"error": f"MCP client error: {str(e)}"}
    
    # Repository methods
    def get_user_repos(self) -> List[Dict[str, Any]]:
        """Get all repositories accessible to the authenticated user"""
        result = self.call_tool_sync("get_user_repos", {})
        if isinstance(result, list):
            return result
        else:
            print(f"Error getting repos: {result}")
            return []
    
    def get_pull_requests(self, repo_name: str, state: str = "open", base: str = "main") -> List[Dict[str, Any]]:
        """Get pull requests for a repository"""
        result = self.call_tool_sync("get_pull_requests", {
            "repo_name": repo_name,
            "state": state,
            "base": base
        })
        if isinstance(result, list):
            return result
        else:
            print(f"Error getting PRs: {result}")
            return []
    
    def get_pr_by_number(self, repo_name: str, pr_number: int) -> Dict[str, Any]:
        """Get a specific pull request by number"""
        result = self.call_tool_sync("get_pr_by_number", {
            "repo_name": repo_name,
            "pr_number": pr_number
        })
        return result
    
    def get_pr_files(self, repo_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """Get files changed in a pull request"""
        result = self.call_tool_sync("get_pr_files", {
            "repo_name": repo_name,
            "pr_number": pr_number
        })
        if isinstance(result, list):
            return result
        else:
            print(f"Error getting PR files: {result}")
            return []
    
    def get_pr_comments(self, repo_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """Get issue comments for a pull request"""
        result = self.call_tool_sync("get_pr_comments", {
            "repo_name": repo_name,
            "pr_number": pr_number
        })
        if isinstance(result, list):
            return result
        else:
            print(f"Error getting PR comments: {result}")
            return []
    
    def get_pr_review_comments(self, repo_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """Get review comments for a pull request"""
        result = self.call_tool_sync("get_pr_review_comments", {
            "repo_name": repo_name,
            "pr_number": pr_number
        })
        if isinstance(result, list):
            return result
        else:
            print(f"Error getting PR review comments: {result}")
            return []
    
    def get_file_content(self, repo_name: str, file_path: str, ref: str = "main") -> Dict[str, Any]:
        """Get content of a file from a repository"""
        result = self.call_tool_sync("get_file_content", {
            "repo_name": repo_name,
            "file_path": file_path,
            "ref": ref
        })
        return result
    
    def create_branch(self, repo_name: str, branch_name: str, base_sha: str) -> Dict[str, Any]:
        """Create a new branch in a repository"""
        result = self.call_tool_sync("create_branch", {
            "repo_name": repo_name,
            "branch_name": branch_name,
            "base_sha": base_sha
        })
        return result
    
    def update_file(self, repo_name: str, file_path: str, message: str, content: str, sha: str, branch: str) -> Dict[str, Any]:
        """Update a file in a repository"""
        result = self.call_tool_sync("update_file", {
            "repo_name": repo_name,
            "file_path": file_path,
            "message": message,
            "content": content,
            "sha": sha,
            "branch": branch
        })
        return result
    
    def create_file(self, repo_name: str, file_path: str, message: str, content: str, branch: str) -> Dict[str, Any]:
        """Create a new file in a repository"""
        result = self.call_tool_sync("create_file", {
            "repo_name": repo_name,
            "file_path": file_path,
            "message": message,
            "content": content,
            "branch": branch
        })
        return result
    
    def create_pull_request(self, repo_name: str, title: str, body: str, head: str, base: str) -> Dict[str, Any]:
        """Create a new pull request"""
        result = self.call_tool_sync("create_pull_request", {
            "repo_name": repo_name,
            "title": title,
            "body": body,
            "head": head,
            "base": base
        })
        return result
    
    def get_branch(self, repo_name: str, branch_name: str) -> Dict[str, Any]:
        """Get information about a branch"""
        result = self.call_tool_sync("get_branch", {
            "repo_name": repo_name,
            "branch_name": branch_name
        })
        return result
    
    def check_branch_exists(self, repo_name: str, branch_name: str) -> Dict[str, Any]:
        """Check if a branch exists in a repository"""
        result = self.call_tool_sync("check_branch_exists", {
            "repo_name": repo_name,
            "branch_name": branch_name
        })
        return result

# Convenience function to create a client instance
def create_github_client() -> GitHubMCPClient:
    """Create and return a GitHub MCP client instance"""
    return GitHubMCPClient()

if __name__ == "__main__":
    # Test the client
    client = create_github_client()
    
    # Test getting user repos
    print("Testing GitHub MCP Client...")
    try:
        repos = client.get_user_repos()
        print(f"✅ Found {len(repos)} repositories")
        for repo in repos[:3]:  # Show first 3 repos
            print(f"  - {repo['full_name']}")
    except Exception as e:
        print(f"❌ Error: {e}") 