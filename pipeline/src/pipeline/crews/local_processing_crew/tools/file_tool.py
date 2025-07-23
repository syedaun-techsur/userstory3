from crewai.tools.base_tool import BaseTool
from typing import Type
from pydantic import BaseModel, Field
import os
import tempfile

class FileSystemToolInput(BaseModel):
    file_path: str = Field(description="Path to the file")
    content: str = Field(default="", description="Content to write to the file")
    operation: str = Field(description="Operation: 'write', 'read', 'exists', 'list_workspace'")

class FileSystemTool(BaseTool):
    name: str = "file_system_tool"
    description: str = "Read, write, check files, and list workspace contents in the filesystem"
    args_schema: Type[BaseModel] = FileSystemToolInput

    def _run(self, file_path: str, content: str = "", operation: str = "read") -> str:
        """Perform file system operations"""
        try:
            print(f"FileSystemTool: Operation={operation}, Path={file_path}, Content length={len(content)}")
            
            # Normalize path to handle both absolute and relative paths
            if file_path.startswith('/workspace/'):
                # Convert absolute workspace paths to local workspace
                relative_path = file_path.replace('/workspace/', '')
                workspace_base = "workspace"
                file_path = os.path.join(workspace_base, relative_path)
                print(f"FileSystemTool: Converted absolute path to local: {file_path}")
            
            if operation == "write":
                # Check if the directory is writable, if not use local workspace
                dir_path = os.path.dirname(file_path)
                if not os.access(dir_path, os.W_OK):
                    # Create local workspace directory
                    workspace_base = "workspace"
                    os.makedirs(workspace_base, exist_ok=True)
                    
                    # Use a local path within workspace
                    file_name = os.path.basename(file_path)
                    local_file_path = os.path.join(workspace_base, "crewai_files", file_name)
                    file_path = local_file_path
                    print(f"FileSystemTool: Using local file path: {file_path}")
                
                # Ensure directory exists
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
                with open(file_path, 'w') as f:
                    f.write(content)
                return f"Successfully wrote content to {file_path}"
                
            elif operation == "read":
                if os.path.exists(file_path):
                    with open(file_path, 'r') as f:
                        content = f.read()
                        print(f"FileSystemTool: Read {len(content)} characters from {file_path}")
                        return content
                else:
                    print(f"FileSystemTool: File {file_path} does not exist")
                    return f"File {file_path} does not exist"
                    
            elif operation == "exists":
                exists = os.path.exists(file_path)
                print(f"FileSystemTool: File {file_path} exists: {exists}")
                if not exists and file_path.startswith('workspace/'):
                    # Provide helpful information about local workspace structure
                    workspace_base = "workspace"
                    if os.path.exists(workspace_base):
                        available_dirs = [d for d in os.listdir(workspace_base) if os.path.isdir(os.path.join(workspace_base, d))]
                        return f"False. Note: Local workspace '{workspace_base}' exists with directories: {available_dirs}"
                return str(exists)
                
            elif operation == "list_workspace":
                # New operation to list workspace contents
                workspace_base = "workspace"
                if os.path.exists(workspace_base):
                    contents = {}
                    for root, dirs, files in os.walk(workspace_base):
                        rel_path = os.path.relpath(root, workspace_base)
                        if rel_path == '.':
                            rel_path = 'root'
                        contents[rel_path] = {
                            'directories': dirs,
                            'files': [f for f in files if not f.startswith('.')]  # Exclude hidden files
                        }
                    return f"Workspace contents: {contents}"
                else:
                    return "Workspace directory does not exist"
                
            else:
                print(f"FileSystemTool: Unknown operation: {operation}")
                return f"Unknown operation: {operation}"
                
        except Exception as e:
            error_msg = f"Error in file operation: {str(e)}"
            print(f"FileSystemTool: {error_msg}")
            return error_msg