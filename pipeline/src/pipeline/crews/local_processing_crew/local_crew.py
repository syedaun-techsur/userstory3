from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List, Dict

# Import our tools
from .tools.git_tool import GitTool
from .tools.build_tool import BuildTool
from .tools.file_tool import FileSystemTool

@CrewBase
class LocalProcessingCrew():
    """Local processing crew for build validation and error correction"""
    
    @agent
    def build_agent(self) -> Agent:
        """Agent specialized in running builds and detecting errors"""
        return Agent(
            config=self.agents_config['build_agent'],
            tools=[GitTool(), FileSystemTool(), BuildTool()],
            verbose=True
        )
    
    @agent
    def error_correction_agent(self) -> Agent:
        """Agent specialized in analyzing build errors and fixing them"""
        return Agent(
            config=self.agents_config['error_correction_agent'],
            tools=[FileSystemTool(), BuildTool()],  # We'll add web search later
            verbose=True
        )
    
    @task
    def validate_builds_task(self) -> Task:
        return Task(
            config=self.tasks_config['validate_builds_task']
        )
    
    @task
    def fix_errors_task(self) -> Task:
        return Task(
            config=self.tasks_config['fix_errors_task'],
            context=[self.validate_builds_task()],
        )
    
    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True
        )
    
    def process_files(self, repo_name: str, pr_number: int, refined_files: Dict) -> Dict:
        """Process refined files through local validation and error correction"""
        context = {
            "repo_name": repo_name,
            "pr_number": pr_number,
            "refined_files": refined_files
        }
        
        result = self.crew().kickoff(context)
        return result