from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai_tools import MCPServerAdapter
from mcp import StdioServerParameters
from typing import List
import os

@CrewBase
class AiRefine():
    """AiRefine crew with real GitHub MCP integration"""


    
    mcp_server_params = [
        StdioServerParameters(
            command="python",
            args=["../github_server.py"],  # Path from src/ to pipeline/github_server.py
            env={"GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", ""), **os.environ},
        ),
    ]

    @agent
    def github_pr_agent(self) -> Agent:
        """GitHub PR Management Agent with real MCP tools"""
        return Agent(
            config=self.agents_config['github_pr_agent'],
            tools=self.get_mcp_tools("get_pr_info", "collect_pr_files", 'fetch_readme_requirements'),
            verbose=False
        )
    

    @task
    def analyze_pr_task(self) -> Task:
        """Analyze PR information and collect files"""
        return Task(
            config=self.tasks_config['analyze_pr_task'],
        )
    

    @crew
    def crew(self) -> Crew:
        """Main AI Refine crew with sequential processing"""
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