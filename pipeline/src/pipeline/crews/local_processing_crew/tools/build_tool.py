# pipeline/src/pipeline/crews/local_processing_crew/tools/build_tool.py
from crewai.tools.base_tool import BaseTool
from typing import Type
from pydantic import BaseModel, Field
import subprocess
import os

class BuildToolInput(BaseModel):
    project_path: str = Field(description="Path to the project directory")
    build_command: str = Field(description="Build command to run (e.g., 'npm install', 'mvn install')")

class BuildTool(BaseTool):
    name: str = "build_tool"
    description: str = "Run build commands and capture output"
    args_schema: Type[BaseModel] = BuildToolInput

    def _run(self, project_path: str, build_command: str) -> str:
        """Run a build command in the specified project directory"""
        try:
            # Change to project directory
            os.chdir(project_path)
            
            # Run the build command
            result = subprocess.run(build_command, shell=True, capture_output=True, text=True)
            
            output = f"Command: {build_command}\n"
            output += f"Return Code: {result.returncode}\n"
            output += f"STDOUT:\n{result.stdout}\n"
            output += f"STDERR:\n{result.stderr}\n"
            
            return output
                
        except Exception as e:
            return f"Error running build command: {str(e)}"