#!/usr/bin/env python3
"""
Complete CrewAI + MCP Integration Example
Demonstrates how to use GitHub MCP Server with CrewAI agents
"""

import os
from crewai import Agent, Task, Crew
from crewai_tools import MCPServerAdapter
from mcp import StdioServerParameters

def create_github_mcp_crew():
    """Create a CrewAI crew with GitHub MCP tools"""
    
    print("🚀 Creating CrewAI Crew with GitHub MCP Integration")
    print("=" * 60)
    
    # Configure the GitHub MCP server
    server_params = StdioServerParameters(
        command="python3",
        args=["github_server.py"],
        env={"UV_PYTHON": "3.12", **os.environ},
    )
    
    # Use MCPServerAdapter to get MCP tools
    with MCPServerAdapter(server_params) as mcp_tools:
        print(f"✅ Connected to GitHub MCP Server")
        print(f"📋 Available tools: {[tool.name for tool in mcp_tools]}")
        
        # Create GitHub PR Manager Agent
        github_agent = Agent(
            role="GitHub PR Manager",
            goal="Manage GitHub pull requests using MCP tools",
            backstory="""I am an AI agent specialized in GitHub operations. 
            I can fetch PR information, collect files, and create new PRs using MCP tools.
            I work efficiently with GitHub's API through the MCP server.""",
            tools=mcp_tools,
            verbose=True
        )
        
        # Create Code Refiner Agent (using filtered tools)
        with MCPServerAdapter(server_params, "collect_pr_files", "fetch_repo_context") as refiner_tools:
            refiner_agent = Agent(
                role="Code Refiner",
                goal="Refine and improve code from GitHub PRs",
                backstory="""I am an AI agent that specializes in code refinement.
                I can collect files from PRs, analyze their context, and suggest improvements.
                I work with the GitHub MCP tools to access repository data.""",
                tools=refiner_tools,
                verbose=True
            )
            
            # Create PR Creator Agent (using specific tools)
            with MCPServerAdapter(server_params, "create_refined_pr") as creator_tools:
                creator_agent = Agent(
                    role="PR Creator",
                    goal="Create new pull requests with refined code",
                    backstory="""I am an AI agent that creates new pull requests.
                    I take refined code and create new branches and PRs using GitHub MCP tools.
                    I ensure proper PR titles, descriptions, and file management.""",
                    tools=creator_tools,
                    verbose=True
                )
                
                # Define tasks
                task1 = Task(
                    description="""Fetch information about PR #123 from repository 'test/repo'.
                    Use the get_pr_info tool to retrieve PR details.""",
                    agent=github_agent,
                    expected_output="PR information including title, status, and basic details"
                )
                
                task2 = Task(
                    description="""Collect all files from PR #123 in repository 'test/repo'.
                    Use the collect_pr_files tool to get the file contents.""",
                    agent=refiner_agent,
                    expected_output="List of files and their contents from the PR"
                )
                
                task3 = Task(
                    description="""Create a new PR with refined code.
                    Use the create_refined_pr tool to create a new branch and PR.
                    Use the following details:
                    - Repository: 'test/repo'
                    - Base branch: 'main'
                    - New branch: 'refined-code-v2'
                    - Title: 'Refined Code Implementation'
                    - Body: 'This PR contains improved and refined code based on analysis.'
                    - Files: {'src/main.py': '# Refined Python code with improvements'}
                    """,
                    agent=creator_agent,
                    expected_output="Confirmation of PR creation with branch name and file count"
                )
                
                # Create the crew
                crew = Crew(
                    agents=[github_agent, refiner_agent, creator_agent],
                    tasks=[task1, task2, task3],
                    verbose=True
                )
                
                print("\n🎯 Starting CrewAI workflow with MCP tools...")
                result = crew.kickoff()
                
                print("\n📋 Workflow Results:")
                print("=" * 40)
                print(result)
                
                return result

def main():
    """Main function to run the CrewAI MCP example"""
    
    print("🌟 CrewAI + MCP Integration Example")
    print("This example demonstrates:")
    print("- GitHub MCP Server integration")
    print("- Multiple agents with different MCP tools")
    print("- Tool filtering for specific agent roles")
    print("- Complete workflow execution")
    print("=" * 60)
    
    try:
        result = create_github_mcp_crew()
        print("\n✅ Example completed successfully!")
        return True
    except Exception as e:
        print(f"\n❌ Example failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1) 