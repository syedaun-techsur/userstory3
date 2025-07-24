from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai_tools import MCPServerAdapter
from mcp import StdioServerParameters
from typing import List
import os
import json
from typing import Dict
    
def collect_files_for_refinement(repo_name: str, pr_number: int, pr_info=None):
    """Direct copy of the working file collection function"""
    from github import Github
    import os
    
    github_direct = Github(os.getenv("GITHUB_TOKEN"))
    if not github_direct:
        print("GitHub API not available")
        return {}
    
    try:
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        
        print(f"Got PR #{pr.number}: {pr.title}")
        
        # Get PR files
        pr_files = []
        for file in pr.get_files():
            pr_files.append({
                "filename": file.filename,
                "status": file.status,
                "additions": file.additions,
                "deletions": file.deletions,
                "changes": file.changes
            })
            
        print(f"Got {len(pr_files)} PR files")
        
        # Filter files
        file_names = set()
        for file in pr_files:
            # Skip lock files in any directory
            if file["filename"].endswith("package-lock.json") or file["filename"].endswith("package.lock.json"):
                print(f"Skipping lock file: {file['filename']}")
                continue
            # Skip GitHub workflow and config files
            if file["filename"].startswith('.github/'):
                print(f"Skipping GitHub workflow or config file: {file['filename']}")
                continue
            # Skip LICENSE files (various formats)
            if file["filename"].upper() in ['LICENSE', 'LICENSE.TXT', 'LICENSE.MD', 'LICENSE.MIT', 'LICENSE.APACHE', 'LICENSE.BSD']:
                print(f"Skipping license file: {file['filename']}")
                continue
            # Skip macOS .DS_Store files
            if file["filename"] == '.DS_Store' or file["filename"].endswith('/.DS_Store'):
                print(f"Skipping .DS_Store file: {file['filename']}")
                continue
            # Skip asset and binary files
            asset_extensions = [
                '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
                '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
                '.mp3', '.wav', '.flac', '.aac', '.ogg',
                '.ttf', '.otf', '.woff', '.woff2', '.eot',
                '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                '.zip', '.rar', '.7z', '.tar', '.gz',
                '.exe', '.dll', '.so', '.dylib'
            ]
            if any(file["filename"].lower().endswith(ext) for ext in asset_extensions):
                print(f"Skipping asset/binary file: {file['filename']}")
                continue
            file_names.add(file["filename"])

        print(f"File names to process: {file_names}")
        
        # Get file contents
        result = {}
        ref = pr_info.get("pr_branch", pr.head.ref) if pr_info else pr.head.ref
        
        for file_name in file_names:
            try:
                print(f"Getting content for {file_name}...")
                file_content = repo.get_contents(file_name, ref=ref)
                
                if isinstance(file_content, list):
                    print(f"Skipping directory {file_name}")
                    continue
                else:
                    content = file_content.decoded_content.decode('utf-8')
                    result[file_name] = content
                    print(f"Successfully got content for {file_name}")
            except Exception as e:
                print(f"Error reading file {file_name}: {e}")
                continue

        print(f"Returning {len(result)} files")
        return result
        
    except Exception as e:
        print(f"Direct API failed: {e}")
        return {}

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

    def run_and_get_files(self, repo_name: str, pr_number: int) -> Dict[str, str]:
        """
        Use the proven file collection logic from step3_regenerate.py
        This bypasses the JSON parsing issues and directly returns the file dictionary.
        """
        try:
            print(f"Using direct file collection for {repo_name} PR #{pr_number}")
            # Use the working function from step3_regenerate.py
            files_dict = collect_files_for_refinement(repo_name, pr_number)
            print(f"Successfully collected {len(files_dict)} files")
            return files_dict
                
        except Exception as e:
            print(f"Error in run_and_get_files: {e}")
            return {}