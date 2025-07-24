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

@CrewBase
class LocalProcessingCrew():
    """Local processing crew for build validation and error correction"""
    
    @agent
    def run_and_test_agent(self) -> Agent:
        """Agent specialized in running builds and analyzing test results"""
        return Agent(
            config=self.agents_config['run_and_test_agent'],
            tools=[FileSystemTool(), BuildTool()],
            verbose=True
        )
        
    @agent
    def fix_code_agent(self) -> Agent:
        """Agent specialized in fixing code based on build errors"""
        return Agent(
            config=self.agents_config['fix_code_agent'],
            tools=[FileSystemTool()], # This agent only needs to modify files
            verbose=True
        )
    
    @task
    def run_and_test_task(self) -> Task:
        """Task to run builds and tests"""
        return Task(
            config=self.tasks_config['run_and_test_task']
        )
    
    @task
    def fix_code_task(self) -> Task:
        """Task to fix code based on build and test failures"""
        return Task(
            config=self.tasks_config['fix_code_task'],
            context=[self.run_and_test_task()],
        )
    
    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=[self.run_and_test_agent(), self.fix_code_agent()],
            tasks=[self.run_and_test_task(), self.fix_code_task()],
            process=Process.sequential,
            verbose=True
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
    
    def process_files(self, repo_name: str, pr_number: int, refined_files: Dict, max_retries: int = 3) -> Dict:
        """Process refined files through local validation and error correction with a retry loop"""
        context = {
            "repo_name": repo_name,
            "pr_number": pr_number,
            "refined_files": refined_files
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
            
            # Detect project types and build file paths in the repository
            print(f"[LocalRepo] 🔍 Detecting project types and build files in repository...")
            
            # Process React/npm projects (FRONTEND PIPELINE)
            package_json_files_list = [f for f in refined_files.keys() if f.endswith("package.json")]
            package_json_paths = {}
            
            if package_json_files_list:
                print(f"[LocalRepo] 🎨 FRONTEND PIPELINE: package.json files detected: {package_json_files_list}")
                
                # Calculate full paths for each package.json
                for package_file in package_json_files_list:
                    # Get the directory containing the package.json
                    package_dir = os.path.dirname(package_file) if os.path.dirname(package_file) else "."
                    package_dir_path = os.path.join(repo_path, package_dir)
                    package_json_paths[package_file] = {
                        "file_path": package_file,
                        "directory_path": package_dir_path,
                        "project_type": "react"
                    }
                    print(f"[LocalRepo] 📦 Package.json '{package_file}' -> Directory: {package_dir_path}")
            else:
                print(f"[LocalRepo] 🎨 No package.json files found - skipping frontend pipeline")
            
            # Process Maven/Java projects (BACKEND PIPELINE)
            pom_xml_files_list = [f for f in refined_files.keys() if f.endswith("pom.xml")]
            pom_xml_paths = {}
            
            if pom_xml_files_list:
                print(f"[LocalRepo] ☕ BACKEND PIPELINE: pom.xml files detected: {pom_xml_files_list}")
                
                # Calculate full paths for each pom.xml
                for pom_file in pom_xml_files_list:
                    # Get the directory containing the pom.xml
                    pom_dir = os.path.dirname(pom_file) if os.path.dirname(pom_file) else "."
                    pom_dir_path = os.path.join(repo_path, pom_dir)
                    pom_xml_paths[pom_file] = {
                        "file_path": pom_file,
                        "directory_path": pom_dir_path,
                        "project_type": "springboot"
                    }
                    print(f"[LocalRepo] 📦 Pom.xml '{pom_file}' -> Directory: {pom_dir_path}")
            else:
                print(f"[LocalRepo] ☕ No pom.xml files found - skipping backend pipeline")
            
            # Add build file information to context
            context["build_files"] = {
                "package_json": package_json_paths,
                "pom_xml": pom_xml_paths,
                "has_frontend": len(package_json_files_list) > 0,
                "has_backend": len(pom_xml_files_list) > 0
            }
            
            print(f"[LocalRepo] 📋 Build context prepared:")
            print(f"[LocalRepo]   - Frontend projects: {len(package_json_paths)}")
            print(f"[LocalRepo]   - Backend projects: {len(pom_xml_paths)}")

            # Build and fix retry loop
            build_result = ""
            for attempt in range(max_retries):
                print(f"--- Build and Test Attempt #{attempt + 1} ---")
                
                # Create and run the build crew
                build_crew = Crew(
                    agents=[self.run_and_test_agent()],
                    tasks=[self.run_and_test_task()],
                    process=Process.sequential,
                    verbose=True
                )
                build_result = build_crew.kickoff(inputs=context)
                
                # Convert CrewOutput to string for context
                build_result_str = str(build_result)
                
                # Check for build success - look for multiple success indicators
                success_indicators = [
                    "Return Code: 0",
                    "Overall build status: SUCCESS",
                    "Build completed successfully",
                    "npm run build completed successfully",
                    "mvn clean install completed successfully"
                ]
                
                build_succeeded = any(indicator in build_result_str for indicator in success_indicators)
                
                if build_succeeded:
                    print("--- Build Succeeded ---")
                    return {"status": "success", "result": build_result_str}

                print(f"--- Build Failed. Attempting to fix... ---")
                
                # Add build errors as string to context
                context['build_errors'] = build_result_str
                
                # Create and run the fix crew
                fix_crew = Crew(
                    agents=[self.fix_code_agent()],
                    tasks=[self.fix_code_task()],
                    process=Process.sequential,
                    verbose=True
                )
                fix_result = fix_crew.kickoff(inputs=context)
                
                print(f"--- Fix Attempt Completed ---")
                print(fix_result)

            print(f"--- Max retries reached. Build failed. ---")
            return {"status": "failure", "final_result": build_result_str}
            
        except Exception as e:
            error_msg = f"Error in local processing: {str(e)}"
            print(f"LocalProcessingCrew: {error_msg}")
            return {"status": "error", "message": error_msg}

        
        