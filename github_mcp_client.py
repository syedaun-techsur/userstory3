#!/usr/bin/env python3
"""
GitHub MCP Client v2.0 - Fixed version with proper session management
Provides a clean interface to GitHub MCP server with robust error handling
"""

import asyncio
import json
import logging
import os
import time
from typing import List, Dict, Any, Optional
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp import StdioServerParameters

# Set up logging - only errors and warnings
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GitHubMCPClient:
    """
    Improved GitHub MCP Client with better session management
    
    This version addresses the common MCP timeout and session termination issues
    by using a more robust connection pattern and proper error handling.
    """
    
    def __init__(self, server_script: str = "github_mcp_server.py", timeout: int = 300):
        """
        Initialize GitHub MCP client
        
        Args:
            server_script: Path to the MCP server script
            timeout: Timeout in seconds for operations (default: 5min)
        """
        self.server_script = os.path.abspath(server_script)
        self.timeout = timeout
        self.server_params = StdioServerParameters(
            command="python",
            args=[self.server_script]
        )
        # logger.info(f"GitHub MCP Client v2.0 initialized with timeout: {timeout}s")
    
    async def _call_tool_with_fresh_session(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Call a tool using a fresh MCP session each time to avoid session issues
        
        This approach creates a new session for each call to avoid the common
        issue where MCP sessions terminate after one call.
        """
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                # logger.info(f"Calling tool {tool_name} (attempt {attempt + 1}/{max_retries})")
                
                # Create fresh session for each call
                async with stdio_client(self.server_params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        # Initialize session
                        await session.initialize()
                        
                        # Call the tool with timeout
                        result = await asyncio.wait_for(
                            session.call_tool(tool_name, arguments),
                            timeout=self.timeout
                        )
                        
                        # Extract content from MCP response
                        if result.content and len(result.content) > 0:
                            content_item = result.content[0]
                            if hasattr(content_item, 'text') and content_item.text:
                                response_data = json.loads(content_item.text)
                                # logger.info(f"Tool {tool_name} completed successfully")
                                return response_data
                            else:
                                logger.error(f"Invalid content format from {tool_name}")
                                return {"error": "Invalid content format"}
                        else:
                            logger.error(f"No content in response from {tool_name}")
                            return {"error": "No content in response"}
            
            except asyncio.TimeoutError:
                logger.warning(f"Tool {tool_name} timed out (attempt {attempt + 1})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    return {"error": f"Tool {tool_name} timed out after {max_retries} attempts"}
            
            except Exception as e:
                logger.error(f"Tool {tool_name} failed: {e} (attempt {attempt + 1})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    return {"error": f"Tool {tool_name} failed: {str(e)}"}
        
        return {"error": f"Tool {tool_name} failed after {max_retries} attempts"}
    
    def call_tool_sync(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Synchronous wrapper for tool calls
        
        Args:
            tool_name: Name of the tool to call
            arguments: Arguments for the tool
            
        Returns:
            Tool result
        """
        try:
            logger.debug(f"Calling tool: {tool_name} with args: {arguments}")
            
            # Check if we're already in an event loop
            try:
                loop = asyncio.get_running_loop()
                # If we are, we need to use a different approach
                import concurrent.futures
                import threading
                
                def run_in_new_loop():
                    # Create a new event loop for this thread
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    try:
                        return new_loop.run_until_complete(self._call_tool_with_fresh_session(tool_name, arguments))
                    finally:
                        new_loop.close()
                
                # Run in a thread with a new event loop
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(run_in_new_loop)
                    return future.result(timeout=self.timeout + 30)  # Add buffer to timeout
                    
            except RuntimeError:
                # No event loop running, safe to use asyncio.run()
                return asyncio.run(self._call_tool_with_fresh_session(tool_name, arguments))
                
        except Exception as e:
            logger.error(f"Tool call failed: {e}")
            return {"error": f"Tool call failed: {str(e)}"}
    
    # GitHub API methods - same interface as before
    
    def get_user_repos(self) -> List[Dict[str, Any]]:
        """Get all repositories accessible to the authenticated user"""
        result = self.call_tool_sync("get_user_repos", {})
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "error" in result:
            logger.error(f"Error getting repos: {result['error']}")
            return []
        else:
            logger.error(f"Unexpected result type: {type(result)}")
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
        elif isinstance(result, dict) and "error" in result:
            logger.error(f"Error getting PRs: {result['error']}")
            return []
        else:
            logger.error(f"Unexpected result type: {type(result)}")
            return []
    
    def get_pr_by_number(self, repo_name: str, pr_number: int) -> Dict[str, Any]:
        """Get a specific pull request by number"""
        result = self.call_tool_sync("get_pr_by_number", {
            "repo_name": repo_name,
            "pr_number": pr_number
        })
        return result if isinstance(result, dict) else {"error": "Invalid response"}
    
    def get_pr_files(self, repo_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """Get files changed in a pull request"""
        logger.info(f"[MCP Client] Getting PR files for {repo_name} PR #{pr_number}...")
        result = self.call_tool_sync("get_pr_files", {
            "repo_name": repo_name,
            "pr_number": pr_number
        })
        if isinstance(result, list):
            logger.info(f"[MCP Client] Successfully retrieved {len(result)} PR files")
            return result
        elif isinstance(result, dict) and "error" in result:
            logger.error(f"[MCP Client] Error getting PR files: {result['error']}")
            return []
        else:
            logger.error(f"[MCP Client] Unexpected result type: {type(result)}")
            return []
    
    def get_pr_comments(self, repo_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """Get issue comments for a pull request"""
        result = self.call_tool_sync("get_pr_comments", {
            "repo_name": repo_name,
            "pr_number": pr_number
        })
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "error" in result:
            logger.error(f"Error getting PR comments: {result['error']}")
            return []
        else:
            return []
    
    def get_pr_review_comments(self, repo_name: str, pr_number: int) -> List[Dict[str, Any]]:
        """Get review comments for a pull request"""
        result = self.call_tool_sync("get_pr_review_comments", {
            "repo_name": repo_name,
            "pr_number": pr_number
        })
        if isinstance(result, list):
            return result
        elif isinstance(result, dict) and "error" in result:
            logger.error(f"Error getting PR review comments: {result['error']}")
            return []
        else:
            return []
    
    def get_file_content(self, repo_name: str, file_path: str, ref: str = "main") -> Dict[str, Any]:
        """Get content of a file from a repository"""
        result = self.call_tool_sync("get_file_content", {
            "repo_name": repo_name,
            "file_path": file_path,
            "ref": ref
        })
        return result if isinstance(result, dict) else {"error": "Invalid response"}
    
    def create_branch(self, repo_name: str, branch_name: str, base_sha: str) -> Dict[str, Any]:
        """Create a new branch in a repository"""
        result = self.call_tool_sync("create_branch", {
            "repo_name": repo_name,
            "branch_name": branch_name,
            "base_sha": base_sha
        })
        return result if isinstance(result, dict) else {"error": "Invalid response"}
    
    def update_file(self, repo_name: str, file_path: str, message: str, content: str, sha: str, branch: str) -> Dict[str, Any]:
        """Update a file in a repository"""
        logger.info(f"[MCP Client] Updating file {file_path} ({len(content)} bytes)")
        
        # For very large files, use longer timeout
        original_timeout = self.timeout
        if len(content) > 500000:  # 500KB
            self.timeout = 600  # 10 minutes for large files
            logger.info(f"[MCP Client] Large file detected, extending timeout to {self.timeout}s")
        
        try:
            result = self.call_tool_sync("update_file", {
                "repo_name": repo_name,
                "file_path": file_path,
                "message": message,
                "content": content,
                "sha": sha,
                "branch": branch
            })
            return result if isinstance(result, dict) else {"error": "Invalid response"}
        finally:
            self.timeout = original_timeout
    
    def create_file(self, repo_name: str, file_path: str, message: str, content: str, branch: str) -> Dict[str, Any]:
        """Create a new file in a repository"""
        logger.info(f"[MCP Client] Creating file {file_path} ({len(content)} bytes)")
        
        # For very large files, use longer timeout
        original_timeout = self.timeout
        if len(content) > 500000:  # 500KB
            self.timeout = 600  # 10 minutes for large files
            logger.info(f"[MCP Client] Large file detected, extending timeout to {self.timeout}s")
        
        try:
            result = self.call_tool_sync("create_file", {
                "repo_name": repo_name,
                "file_path": file_path,
                "message": message,
                "content": content,
                "branch": branch
            })
            return result if isinstance(result, dict) else {"error": "Invalid response"}
        finally:
            self.timeout = original_timeout
    
    def create_pull_request(self, repo_name: str, title: str, body: str, head: str, base: str) -> Dict[str, Any]:
        """Create a new pull request"""
        result = self.call_tool_sync("create_pull_request", {
            "repo_name": repo_name,
            "title": title,
            "body": body,
            "head": head,
            "base": base
        })
        return result if isinstance(result, dict) else {"error": "Invalid response"}
    
    def get_branch(self, repo_name: str, branch_name: str) -> Dict[str, Any]:
        """Get information about a branch"""
        result = self.call_tool_sync("get_branch", {
            "repo_name": repo_name,
            "branch_name": branch_name
        })
        return result if isinstance(result, dict) else {"error": "Invalid response"}
    
    def check_branch_exists(self, repo_name: str, branch_name: str) -> Dict[str, Any]:
        """Check if a branch exists in a repository"""
        result = self.call_tool_sync("check_branch_exists", {
            "repo_name": repo_name,
            "branch_name": branch_name
        })
        return result if isinstance(result, dict) else {"error": "Invalid response"}

# Convenience function to create a client instance
def create_github_client(timeout: int = 300) -> GitHubMCPClient:
    """Create and return a GitHub MCP client instance with custom timeout"""
    return GitHubMCPClient(timeout=timeout)

if __name__ == "__main__":
    # Test the client
    client = create_github_client()
    
    # Test getting user repos
    print("Testing GitHub MCP Client v2.0...")
    try:
        repos = client.get_user_repos()
        print(f"✅ Found {len(repos)} repositories")
        for repo in repos[:3]:  # Show first 3 repos
            print(f"  - {repo['full_name']}")
    except Exception as e:
        print(f"❌ Error: {e}") 