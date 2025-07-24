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
            # Normalize path to handle both absolute and relative paths
            if file_path.startswith('/workspace/'):
                # Convert absolute workspace paths to local workspace
                relative_path = file_path.replace('/workspace/', '')
                workspace_base = "workspace"
                file_path = os.path.join(workspace_base, relative_path)
                print(f"FileSystemTool: Converted absolute path to local: {file_path}")
            
            if operation == "read":
                return self._read_file(file_path)
            elif operation == "write":
                return self._write_file(file_path, content)
            elif operation == "exists":
                return self._check_file_exists(file_path)
            elif operation == "list_workspace":
                return self._list_workspace(file_path)
            else:
                return f"Error: Unknown operation '{operation}'. Supported operations: read, write, exists, list_workspace"
                
        except Exception as e:
            error_msg = f"Error in file system operation: {str(e)}"
            print(f"FileSystemTool: {error_msg}")
            return error_msg
    
    def _read_file(self, file_path: str) -> str:
        """Read content from a file"""
        try:
            if not os.path.exists(file_path):
                return f"Error: File does not exist: {file_path}"
            
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            print(f"FileSystemTool: Successfully read file: {file_path}")
            return f"File content for {file_path}:\n{content}"
            
        except Exception as e:
            return f"Error reading file {file_path}: {str(e)}"
    
    def _write_file(self, file_path: str, content: str) -> str:
        """Write content to a file"""
        try:
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            print(f"FileSystemTool: Successfully wrote to file: {file_path}")
            return f"Successfully wrote content to {file_path}"
            
        except Exception as e:
            return f"Error writing to file {file_path}: {str(e)}"
    
    def _check_file_exists(self, file_path: str) -> str:
        """Check if a file exists"""
        try:
            exists = os.path.exists(file_path)
            result = "exists" if exists else "does not exist"
            print(f"FileSystemTool: File {file_path} {result}")
            return f"File {file_path} {result}"
            
        except Exception as e:
            return f"Error checking file existence {file_path}: {str(e)}"
    
    def _list_workspace(self, workspace_path: str = "workspace") -> str:
        """List contents of workspace directory"""
        try:
            if not os.path.exists(workspace_path):
                return f"Error: Workspace directory does not exist: {workspace_path}"
            
            contents = []
            for root, dirs, files in os.walk(workspace_path):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                
                rel_path = os.path.relpath(root, workspace_path)
                if rel_path == '.':
                    rel_path = 'root'
                
                contents.append(f"\n📁 {rel_path}/")
                
                # List subdirectories
                for dir_name in sorted(dirs):
                    contents.append(f"  📁 {dir_name}/")
                
                # List files
                for file_name in sorted(files):
                    file_path = os.path.join(root, file_name)
                    file_size = os.path.getsize(file_path)
                    contents.append(f"  📄 {file_name} ({file_size} bytes)")
            
            result = "Workspace contents:\n" + "\n".join(contents)
            print(f"FileSystemTool: Listed workspace contents for: {workspace_path}")
            return result
            
        except Exception as e:
            return f"Error listing workspace {workspace_path}: {str(e)}"
      
       