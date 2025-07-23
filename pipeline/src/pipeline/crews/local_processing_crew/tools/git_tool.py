# pipeline/src/pipeline/crews/local_processing_crew/tools/git_tool.py
from crewai.tools.base_tool import BaseTool
from typing import Type
from pydantic import BaseModel, Field
import subprocess
import os
import tempfile

class GitToolInput(BaseModel):
    repo_url: str = Field(description="GitHub repository URL")
    branch: str = Field(description="Branch to clone")
    workspace_path: str = Field(description="Path to clone the repository to")

class GitTool(BaseTool):
    name: str = "git_tool"
    description: str = "Clone repositories and manage Git operations"
    args_schema: Type[BaseModel] = GitToolInput

    def _run(self, repo_url: str, branch: str, workspace_path: str) -> str:
        """Clone a repository to the specified workspace"""
        try:
            print(f"GitTool: Cloning {repo_url} branch {branch} to {workspace_path}")
            
            # Extract repo name from URL for workspace naming
            repo_name = repo_url.split('/')[-1].replace('.git', '')
            
            # Create persistent workspace directory like in step3_regenerate.py
            workspace_base = "workspace"
            os.makedirs(workspace_base, exist_ok=True)
            
            # Use repo name for unique workspace (without PR number since we don't have it here)
            safe_repo_name = repo_name.replace('/', '_').replace('\\', '_')
            local_workspace = os.path.join(workspace_base, f"{safe_repo_name}_{branch}")
            
            # If the original workspace_path is not writable, use our local workspace
            if not os.access(os.path.dirname(workspace_path), os.W_OK):
                workspace_path = local_workspace
                print(f"GitTool: Using local workspace: {workspace_path}")
            
            # Check if repository already exists
            git_dir = os.path.join(workspace_path, '.git')
            if os.path.exists(git_dir):
                print(f"GitTool: Repository exists, pulling latest changes: {workspace_path}")
                # Change to the repository directory and pull latest changes
                original_dir = os.getcwd()
                try:
                    os.chdir(workspace_path)
                    # Fetch and reset to latest
                    fetch_cmd = f"git fetch origin {branch}"
                    reset_cmd = f"git reset --hard origin/{branch}"
                    clean_cmd = "git clean -fd"  # Remove untracked files and directories
                    
                    print(f"GitTool: Running: {fetch_cmd}")
                    result = subprocess.run(fetch_cmd, shell=True, capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"GitTool: Fetch failed: {result.stderr}")
                    
                    print(f"GitTool: Running: {reset_cmd}")
                    result = subprocess.run(reset_cmd, shell=True, capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"GitTool: Reset failed: {result.stderr}")
                    
                    print(f"GitTool: Running: {clean_cmd}")
                    result = subprocess.run(clean_cmd, shell=True, capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"GitTool: Clean failed: {result.stderr}")
                    
                    print(f"GitTool: Successfully updated repository at {workspace_path}")
                    return f"Successfully updated {repo_url} branch {branch} at {workspace_path}"
                    
                finally:
                    os.chdir(original_dir)
            else:
                # Create workspace directory if it doesn't exist
                os.makedirs(workspace_path, exist_ok=True)
                
                # Clone the repository fresh
                cmd = f"git clone -b {branch} {repo_url} {workspace_path}"
                print(f"GitTool: Running command: {cmd}")
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"GitTool: Successfully cloned to {workspace_path}")
                    return f"Successfully cloned {repo_url} branch {branch} to {workspace_path}"
                else:
                    error_msg = f"Error cloning repository: {result.stderr}"
                    print(f"GitTool: {error_msg}")
                    return error_msg
                
        except Exception as e:
            error_msg = f"Error in git operation: {str(e)}"
            print(f"GitTool: {error_msg}")
            return error_msg