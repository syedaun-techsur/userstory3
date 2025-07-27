from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List, Dict
import os
from git import Repo  # type: ignore
from dotenv import load_dotenv
from github import Github, Auth

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

from .tools.build_tool import BuildTool
from .tools.file_tool import FileSystemTool
from crewai_tools import SerperDevTool, FileReadTool, FileWriterTool, DirectoryReadTool

@CrewBase
class LocalProcessingCrew():
    """Local processing crew for build validation and error correction"""
    
    @agent
    def frontend_agent(self) -> Agent:
        """Agent specialized in frontend/react builds, testing, and error fixing"""
        return Agent(
            config=self.agents_config['frontend_agent'],
            tools=[BuildTool(), SerperDevTool(), FileReadTool(), FileWriterTool(), DirectoryReadTool()],
            verbose=True,
            cache=False
        )
        
    @agent
    def backend_agent(self) -> Agent:
        """Agent specialized in backend/maven builds, testing, and error fixing"""
        return Agent(
            config=self.agents_config['backend_agent'],
            tools=[BuildTool(), SerperDevTool(), FileReadTool(), FileWriterTool(), DirectoryReadTool()],
            verbose=True,
            cache=False
        )
    
    @task
    def frontend_task(self) -> Task:
        """Task to build, test, and fix frontend/react projects"""
        return Task(
            config=self.tasks_config['frontend_task']
        )
    
    @task
    def backend_task(self) -> Task:
        """Task to build, test, and fix backend/maven projects"""
        return Task(
            config=self.tasks_config['backend_task']
        )
    
    @crew
    def crew(self) -> Crew:
        """Main Code Refinement crew with sequential processing"""
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
            cache=False,
        )

    # Project type detection
    @staticmethod
    def detect_project_type(directory_path: str) -> str:
        """
        Detect if a directory contains a React/Node.js project or Spring Boot/Maven project.
        Returns: 'react', 'springboot', 'mixed', or 'unknown'
        """
        try:
            has_package_json = os.path.exists(os.path.join(directory_path, 'package.json'))
            has_pom_xml = os.path.exists(os.path.join(directory_path, 'pom.xml'))
            
            if has_package_json and has_pom_xml:
                return 'mixed'
            elif has_package_json:
                return 'react'
            elif has_pom_xml:
                return 'springboot'
            else:
                return 'unknown'
        except Exception as e:
            print(f"[Step3] ⚠️ Error detecting project type for {directory_path}: {e}")
            return 'unknown'
    
    @staticmethod
    def detect_project_types_in_repo(self, repo_path: str) -> Dict[str, str]:
        """
        Detect project types in all subdirectories of a repository.
        Returns dict mapping directory paths to project types.
        """
        project_types = {}
        
        try:
            for root, dirs, files in os.walk(repo_path):
                # Skip node_modules and target directories
                dirs[:] = [d for d in dirs if d not in ['node_modules', 'target', '.git']]
                
                project_type = self.detect_project_type(root)
                if project_type != 'unknown':
                    rel_path = os.path.relpath(root, repo_path)
                    if rel_path == '.':
                        rel_path = ''
                    project_types[rel_path] = project_type
                    print(f"[Step3] 📁 Detected {project_type} project in: {rel_path or 'root'}")
        
        except Exception as e:
            print(f"[Step3] ⚠️ Error scanning repository for project types: {e}")
        
        return project_types

    @staticmethod
    def get_persistent_workspace(repo_name, pr_number):
        """Get or create persistent workspace for this PR"""
        # Create workspace directory
        workspace_base = "workspace"
        os.makedirs(workspace_base, exist_ok=True)
        
        # Use repo name and PR number for unique workspace
        safe_repo_name = repo_name.replace('/', '_').replace('\\', '_')
        workspace_dir = os.path.join(workspace_base, f"{safe_repo_name}_PR{pr_number}")
        return workspace_dir
    
    @staticmethod
    def get_pr_branch(repo_name: str, pr_number: int) -> str:
        """Extract the branch name for a given PR"""
        try:
            from github import Github
            
            if not GITHUB_TOKEN:
                raise ValueError("GITHUB_TOKEN environment variable not set")
            
            github_direct = Github(auth=Auth.Token(GITHUB_TOKEN))
            repo = github_direct.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            return pr.head.ref
        
        except Exception as e:
            raise Exception(f"Error extracting PR branch: {str(e)}")
    
    def process_files(self, repo_name: str, pr_number: int, refined_files: Dict) -> Dict:
        """Process refined files through local validation and error correction with a retry loop"""
        refined_file_keys = list(refined_files.keys())
        context = {
            "repo_name": repo_name,
            "pr_number": pr_number,
            "refined_files": refined_file_keys
        }
        
        try:
            # Extract PR branch information
            pr_branch = self.get_pr_branch(repo_name, pr_number)
            print(f"GitTool: Extracted branch '{pr_branch}' for PR #{pr_number}")
            
            workspace_dir = self.get_persistent_workspace(repo_name, pr_number)
            
            # Clone or update the repository
            if os.path.exists(workspace_dir):
                print(f"GitTool: Updating existing workspace: {workspace_dir}")
                try:
                    repo = Repo(workspace_dir)
                    repo.remotes.origin.pull()
                except Exception as e:
                    print(f"GitTool: Error updating workspace, recreating: {e}")
                    import shutil
                    shutil.rmtree(workspace_dir)
                    repo_url = f"https://{GITHUB_TOKEN}@github.com/{repo_name}.git"
                    repo = Repo.clone_from(repo_url, workspace_dir, branch=pr_branch)
            else:
                print(f"GitTool: Creating new workspace: {workspace_dir}")
                repo_url = f"https://{GITHUB_TOKEN}@github.com/{repo_name}.git"
                repo = Repo.clone_from(repo_url, workspace_dir, branch=pr_branch)
            
            print(f"GitTool: Successfully processed repository at {workspace_dir}")
            repo_path = workspace_dir

            # Apply all LLM changes to local files
            print(f"[LocalRepo] Applying LLM changes to {len(refined_files)} files...")
            
            for file_path, file_data in refined_files.items():
                local_file_path = os.path.join(repo_path, file_path)
                
                # Create directories if they don't exist
                os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                
                # Write the LLM-refined content to the local file
                with open(local_file_path, "w", encoding="utf-8") as f:
                    f.write(file_data["updated_code"])
                
                print(f"[LocalRepo] ✓ Applied LLM changes to {file_path}")
                
            context["workspace_path"] = workspace_dir
            self.crew().kickoff(context)
            

            
    
        
        except Exception as e:
            error_msg = f"Error in local processing: {str(e)}"
            print(f"LocalProcessingCrew: {error_msg}")
            return {"status": "error", "message": error_msg}

        
        