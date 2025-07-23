from crewai.tools.base_tool import BaseTool
from typing import Type
from pydantic import BaseModel, Field
import os

class FileSystemToolInput(BaseModel):
    file_path: str = Field(description="Path to the file")
    content: str = Field(description="Content to write to the file")
    operation: str = Field(description="Operation: 'write', 'read', 'exists'")

class FileSystemTool(BaseTool):
    name: str = "file_system_tool"
    description: str = "Read, write, and check files in the filesystem"
    args_schema: Type[BaseModel] = FileSystemToolInput

    def _run(self, file_path: str, content: str = "", operation: str = "read") -> str:
        """Perform file system operations"""
        try:
            if operation == "write":
                # Ensure directory exists
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
                with open(file_path, 'w') as f:
                    f.write(content)
                return f"Successfully wrote content to {file_path}"
                
            elif operation == "read":
                if os.path.exists(file_path):
                    with open(file_path, 'r') as f:
                        return f.read()
                else:
                    return f"File {file_path} does not exist"
                    
            elif operation == "exists":
                return str(os.path.exists(file_path))
                
            else:
                return f"Unknown operation: {operation}"
                
        except Exception as e:
            return f"Error in file operation: {str(e)}"