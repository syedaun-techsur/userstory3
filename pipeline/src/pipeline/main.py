#!/usr/bin/env python
from random import randint

from pydantic import BaseModel

from crewai.flow import Flow, listen, start

from typing import Dict, Any

from pipeline.crews.refine_crew.refine_crew import CodeRefinementCrew
from pipeline.crews.local_processing_crew.local_crew import LocalProcessingCrew
from pipeline.response_extraction import extract_changes, extract_updated_code, cleanup_extracted_code

import sys
from github import Github
import os
import tempfile
import shutil

def collect_files_for_refinement(repo_name: str, pr_number: int, pr_info=None):
    """Direct copy of the working file collection function"""
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


def create_local_workspace_for_webhook(project_name: str, files_dict: Dict[str, str]) -> str:
    """Create a local workspace directory with the webhook files"""
    # Create workspace directory
    workspace_base = "workspace"
    os.makedirs(workspace_base, exist_ok=True)
    
    # Use project name for unique workspace
    safe_project_name = project_name.replace('/', '_').replace('\\', '_').replace(' ', '_')
    workspace_dir = os.path.join(workspace_base, f"webhook_{safe_project_name}")
    
    # Remove existing workspace if it exists
    if os.path.exists(workspace_dir):
        shutil.rmtree(workspace_dir)
    
    # Create fresh workspace
    os.makedirs(workspace_dir, exist_ok=True)
    
    # Write all files to the workspace
    for file_path, file_content in files_dict.items():
        local_file_path = os.path.join(workspace_dir, file_path)
        
        # Create directories if they don't exist
        os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
        
        # Write the file content
        with open(local_file_path, "w", encoding="utf-8") as f:
            f.write(file_content)
        
        print(f"Created file: {file_path}")
    
    print(f"Created workspace at: {workspace_dir}")
    return workspace_dir


def process_external_pipeline_files(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process files received from external webhook"""
    try:
        project_name = webhook_data.get('project_name', 'unknown-project')
        files_dict = webhook_data.get('files', {})
        
        if not files_dict:
            return {"error": "No files provided", "status": "failed"}
        
        print(f"Processing webhook files for project: {project_name}")
        print(f"Files to process: {list(files_dict.keys())}")
        
        # Create local workspace with the files
        workspace_dir = create_local_workspace_for_webhook(project_name, files_dict)
        
        # Run the pipeline using WebhookFlow
        webhook_flow = WebhookFlow(
            files_dict=files_dict,
            project_name=project_name,
            workspace_dir=workspace_dir
        )
        
        result = webhook_flow.kickoff()
        
        return {
            "status": "success",
            "project_name": project_name,
            "processed_files": len(files_dict),
            "workspace_dir": workspace_dir,
            "result": result
        }
        
    except Exception as e:
        print(f"Error processing external pipeline files: {str(e)}")
        return {"error": str(e), "status": "failed"}


class PoemState(BaseModel):
    repo: str
    pr_number : int
    files_dict: dict = None  # Store the file dictionary
    refined_code: dict = None  # Store the refined code


class WebhookState(BaseModel):
    project_name: str
    workspace_dir: str = None
    files_dict: dict = None  # Store the file dictionary
    refined_code: dict = None  # Store the refined code


class PoemFlow(Flow[PoemState]):
    def __init__(self, files_dict: dict, repo: str, pr_number: int):
        self._repo = repo
        self._pr_number = pr_number
        self._files_dict = files_dict
        
        super().__init__()
    
    def _create_initial_state(self):
        return PoemState(repo=self._repo, pr_number=self._pr_number, files_dict=self._files_dict)
       
    
    @start()
    def refine_code(self):
        """Step 2: Refine the code using refine crew - one file at a time"""
        files_dict = self.state.files_dict
        if not files_dict:
            self.state.refined_code = {}
            return {}

        refine_crew = CodeRefinementCrew()
        refined_files = {}
        for file_path, file_content in files_dict.items():
            refined_single_file = refine_crew.refine_files({file_path: file_content})
            raw_response = refined_single_file.get(file_path, file_content)
            
            # Extract changes and updated code from raw response
            changes = extract_changes(raw_response, file_path)
            updated_code = extract_updated_code(raw_response)
            updated_code = cleanup_extracted_code(updated_code)
            
            # Store structured result
            refined_files[file_path] = {
                "old_code": file_content,
                "changes": changes,
                "updated_code": updated_code,
            }
            print(f"Refined: {file_path}")

        self.state.refined_code = refined_files
        return refined_files
    
    @listen(refine_code)
    def local_processing(self):
        """Step 3: Local processing with build validation and error correction"""
        refined_files = self.state.refined_code
        
        if not refined_files:
            return {}
        
        # Create local processing crew
        local_crew = LocalProcessingCrew()
        
        # Process files through local validation
        processed_result = local_crew.process_files(
            repo_name=self.state.repo,
            pr_number=self.state.pr_number,
            refined_files=refined_files
        )
        
        # For now, return the refined files with a placeholder for build status
        # We'll enhance this once the crew is working
        processed_files = {}
        for file_path, file_result in refined_files.items():
            processed_files[file_path] = {
                "old_code": file_result["old_code"],
                "changes": file_result["changes"], 
                "updated_code": file_result["updated_code"],
                "build_status": "pending",  # Will be updated by the crew
                "error_corrections": []
            }
        
        return processed_files


class WebhookFlow(Flow[WebhookState]):
    def __init__(self, files_dict: dict, project_name: str, workspace_dir: str):
        self._project_name = project_name
        self._workspace_dir = workspace_dir
        self._files_dict = files_dict
        
        super().__init__()
    
    def _create_initial_state(self):
        return WebhookState(
            project_name=self._project_name, 
            workspace_dir=self._workspace_dir,
            files_dict=self._files_dict
        )
    
    @start()
    def refine_code(self):
        """Step 1: Refine the code using refine crew - one file at a time"""
        files_dict = self.state.files_dict
        if not files_dict:
            self.state.refined_code = {}
            return {}

        refine_crew = CodeRefinementCrew()
        refined_files = {}
        for file_path, file_content in files_dict.items():
            refined_single_file = refine_crew.refine_files({file_path: file_content})
            raw_response = refined_single_file.get(file_path, file_content)
            
            # Extract changes and updated code from raw response
            changes = extract_changes(raw_response, file_path)
            updated_code = extract_updated_code(raw_response)
            updated_code = cleanup_extracted_code(updated_code)
            
            # Store structured result
            refined_files[file_path] = {
                "old_code": file_content,
                "changes": changes,
                "updated_code": updated_code,
            }
            print(f"Refined: {file_path}")

        self.state.refined_code = refined_files
        return refined_files
    
    @listen(refine_code)
    def local_processing(self):
        """Step 2: Apply refined code to local workspace and run full validation"""
        refined_files = self.state.refined_code
        workspace_dir = self.state.workspace_dir
        
        if not refined_files:
            return {}
        
        print(f"Applying refined code to workspace: {workspace_dir}")
        
        # Apply refined code to the local workspace
        for file_path, file_data in refined_files.items():
            local_file_path = os.path.join(workspace_dir, file_path)
            
            # Create directories if they don't exist
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
            
            # Write the refined content to the local file
            with open(local_file_path, "w", encoding="utf-8") as f:
                f.write(file_data["updated_code"])
            
            print(f"Applied refined code to: {file_path}")
        
        # Create local processing crew for validation (npm install, build validation, etc.)
        local_crew = LocalProcessingCrew()
        
        # For webhook processing, we bypass GitHub operations and run validation directly
        try:
            # Prepare context for the crew (similar to LocalProcessingCrew.process_files)
            refined_file_keys = list(refined_files.keys())
            context = {
                "repo_name": f"webhook/{self.state.project_name}",
                "pr_number": 0,
                "refined_files": refined_file_keys,
                "workspace_path": workspace_dir  # Use our webhook workspace
            }
            
            print(f"Running validation crew on workspace: {workspace_dir}")
            
            # Run the crew validation directly (this includes npm install, build validation, etc.)
            crew_output = local_crew.crew().kickoff(context)
            
            # Extract result from CrewOutput object for JSON serialization
            if hasattr(crew_output, 'raw'):
                processed_result = crew_output.raw
            else:
                processed_result = str(crew_output)
            
            print(f"Validation completed successfully for {self.state.project_name}")
            
        except Exception as e:
            error_msg = f"Error in webhook validation: {str(e)}"
            print(f"WebhookFlow: {error_msg}")
            processed_result = {"status": "error", "message": error_msg}
        
        print(f"LocalProcessingCrew validation completed for {self.state.project_name}")
        
        # Return processed files with validation results
        processed_files = {}
        for file_path, file_result in refined_files.items():
            processed_files[file_path] = {
                "old_code": file_result["old_code"],
                "changes": file_result["changes"], 
                "updated_code": file_result["updated_code"],
                "local_path": os.path.join(workspace_dir, file_path),
                "status": "processed",
                "validation_status": "completed"  # Indicates LocalProcessingCrew ran
            }
        
        return {
            "processed_files": processed_files,
            "workspace_dir": workspace_dir,
            "total_files": len(processed_files),
            "validation_result": processed_result
        }


def kickoff(files_dict: dict, repo: str, pr_number: int):
    poem_flow = PoemFlow(files_dict=files_dict,repo=repo,pr_number=pr_number)
    poem_flow.kickoff()


def plot():
    poem_flow = PoemFlow()
    poem_flow.plot()


if __name__ == "__main__":
   # Check if correct number of arguments provided
    repo_name = sys.argv[1]
    pr_number = int(sys.argv[2])
    files_dict = collect_files_for_refinement(repo_name, pr_number)
    
    
    
    kickoff(files_dict,repo_name, pr_number)

