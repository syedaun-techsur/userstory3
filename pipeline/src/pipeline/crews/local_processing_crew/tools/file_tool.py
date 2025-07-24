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
      
       