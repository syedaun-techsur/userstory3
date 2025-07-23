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
            # Create workspace directory if it doesn't exist
            os.makedirs(workspace_path, exist_ok=True)
            
            # Clone the repository
            cmd = f"git clone -b {branch} {repo_url} {workspace_path}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode == 0:
                return f"Successfully cloned {repo_url} branch {branch} to {workspace_path}"
            else:
                return f"Error cloning repository: {result.stderr}"
                
        except Exception as e:
            return f"Error in git operation: {str(e)}"