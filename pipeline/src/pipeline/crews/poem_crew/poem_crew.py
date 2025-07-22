from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai_tools import MCPServerAdapter
from mcp import StdioServerParameters
from typing import List, Dict
import os
import json

@CrewBase
class PRFetch():
    """File Collection crew with real GitHub MCP integration"""

    mcp_server_params = [
        StdioServerParameters(
            command="python",
            args=["../github_server.py"],  # Path from src/ to pipeline/github_server.py
            env={"GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", ""), **os.environ},
        ),
    ]

    @agent
    def github_pr_agent(self) -> Agent:
        """GitHub File Collection Agent with real MCP tools"""
        return Agent(
            config=self.agents_config['github_pr_agent'],
            tools=self.get_mcp_tools("collect_pr_files"),  # Only need collect_pr_files
            verbose=False
        )
    

    @task
    def analyze_pr_task(self) -> Task:
        """Collect files from PR and return as JSON"""
        return Task(
            config=self.tasks_config['analyze_pr_task'],
        )
    

    @crew
    def crew(self) -> Crew:
        """File Collection crew with sequential processing"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

    def get_mcp_tools(self, *tool_names):
        """Get MCP tools from the GitHub server"""
        try:
            with MCPServerAdapter(self.mcp_server_params[0]) as mcp_tools:
                # Filter tools by name if specified
                if tool_names:
                    filtered_tools = [tool for tool in mcp_tools if tool.name in tool_names]
                    return filtered_tools
                return mcp_tools
        except Exception as e:
            print(f"Error getting MCP tools: {e}")
            return []
    
    def run_and_get_files(self, repo_name: str, pr_number: int) -> Dict[str, str]:
        """
        Run the crew and return the file collection result as a dictionary.
        This ensures the output is in the exact format needed for the next agent.
        """
        try:
            # Set the context for the task
            context = {
                "repo_name": repo_name,
                "pr_number": pr_number
            }
            
            # Run the crew
            result = self.crew().kickoff(context)
            
            # Parse the result to ensure it's the correct format
            if isinstance(result, str):
                # Try to parse as JSON
                try:
                    parsed_result = json.loads(result)
                    if isinstance(parsed_result, dict):
                        return parsed_result
                    else:
                        raise ValueError("Result is not a dictionary")
                except json.JSONDecodeError:
                    raise ValueError("Result is not valid JSON")
            elif isinstance(result, dict):
                return result
            else:
                raise ValueError(f"Unexpected result type: {type(result)}")
                
        except Exception as e:
            print(f"Error in run_and_get_files: {e}")
            return {}