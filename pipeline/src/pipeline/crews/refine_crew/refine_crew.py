from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List, Dict
import os
import json
import re

@CrewBase
class CodeRefinementCrew():

    @agent
    def code_refiner_agent(self) -> Agent:
        """Code Refinement Agent with MCP tools"""
        return Agent(
            config=self.agents_config['code_refiner_agent'],
            verbose=False
        )

    @task
    def refine_code_task(self) -> Task:
        """Refine code with specific output format"""
        return Task(
            config=self.tasks_config['refine_code_task'],
        )

    @crew
    def crew(self) -> Crew:
        """Main Code Refinement crew with sequential processing"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )
    
    def refine_files(self, files_dict: Dict[str, str]) -> Dict[str, str]:
        """
        Refine a single file in the files_dict and return it in the same format.
        This ensures the output is in the exact format needed for the next step.
        """
        try:
            if not files_dict:
                print("No files to refine")
                return {}
            
            # Validate that we're processing exactly one file
            if len(files_dict) != 1:
                print(f"Warning: Expected 1 file, got {len(files_dict)} files")
            
            file_path = list(files_dict.keys())[0]
            file_content = files_dict[file_path]
            
            # Extract file extension for proper code block formatting
            file_extension = file_path.split('.')[-1].lower() if '.' in file_path else 'txt'
            
            print(f"🔧 Refining single file: {file_path} (extension: {file_extension})")
            
            # Set the context for the task with variables for substitution
            context = {
                'file_path': file_path,
                'file_content': file_content,
                'file_extension': file_extension,
                'files_dict': files_dict  # Keep original for backward compatibility
            }
            
            # Run the crew
            result = self.crew().kickoff(context)
            
            # Handle CrewOutput objects (newer CrewAI versions)
            if hasattr(result, 'raw'):
                # Extract the raw result from CrewOutput
                result = result.raw
            
            # Return the raw LLM output as a string
            if isinstance(result, str):
                print(f"✅ Successfully refined file: {file_path}")
                return {file_path: result}
            elif isinstance(result, dict):
                print(f"✅ Successfully refined file: {file_path}")
                return result
            else:
                raise ValueError(f"Unexpected result type: {type(result)}")
                
        except Exception as e:
            print(f"Error in refine_files: {e}")
            return files_dict  # Return original file if refinement fails