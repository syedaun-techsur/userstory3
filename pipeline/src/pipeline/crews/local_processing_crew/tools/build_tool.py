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
    
    def cache_function(self, *args, **kwargs) -> bool:
        return False
    
    def _run(self, project_path: str, build_command: str) -> str:
        """Run a build command in the specified project directory"""
        try:
            print(f"BuildTool: Running '{build_command}' in '{project_path}'")
            
            # Normalize path to handle both absolute and relative paths
            if project_path.startswith('/workspace/'):
                # Convert absolute workspace paths to local workspace
                relative_path = project_path.replace('/workspace/', '')
                workspace_base = "workspace"
                project_path = os.path.join(workspace_base, relative_path)
                print(f"BuildTool: Converted absolute path to local: {project_path}")
            
            # Check if project path exists
            if not os.path.exists(project_path):
                # Try to find the project in local workspace
                workspace_base = "workspace"
                if os.path.exists(workspace_base):
                    # Look for the project in workspace subdirectories
                    available_projects = []
                    for root, dirs, files in os.walk(workspace_base):
                        for dir_name in dirs:
                            # Check if this directory contains build files
                            dir_path = os.path.join(root, dir_name)
                            if any(os.path.exists(os.path.join(dir_path, f)) for f in ['package.json', 'pom.xml', 'build.gradle', 'Makefile']):
                                available_projects.append(dir_path)
                    
                    if available_projects:
                        error_msg = f"Error: Project path {project_path} does not exist. Available projects in workspace: {available_projects}"
                    else:
                        error_msg = f"Error: Project path {project_path} does not exist. No build projects found in workspace."
                    print(f"BuildTool: {error_msg}")
                    return error_msg
                else:
                    error_msg = f"Error: Project path {project_path} does not exist and workspace directory not found"
                    print(f"BuildTool: {error_msg}")
                    return error_msg
            
            # Store current directory to restore later
            original_dir = os.getcwd()
            
            try:
                # Change to project directory
                os.chdir(project_path)
                
                # Run the build command
                result = subprocess.run(build_command, shell=True, capture_output=True, text=True)
                
                output = f"Command: {build_command}\n"
                output += f"Working Directory: {project_path}\n"
                output += f"Return Code: {result.returncode}\n"
                output += f"STDOUT:\n{result.stdout}\n"
                output += f"STDERR:\n{result.stderr}\n"
                
                print(f"BuildTool: Command completed with return code {result.returncode}")
                return output
            finally:
                # Restore original directory
                os.chdir(original_dir)
                
        except Exception as e:
            error_msg = f"Error running build command: {str(e)}"
            print(f"BuildTool: {error_msg}")
            return error_msg