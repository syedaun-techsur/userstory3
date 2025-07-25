#!/usr/bin/env python
from random import randint

from pydantic import BaseModel

from crewai.flow import Flow, listen, start

from pipeline.crews.refine_crew.refine_crew import CodeRefinementCrew
from pipeline.crews.local_processing_crew.local_crew import LocalProcessingCrew
from pipeline.response_extraction import extract_changes, extract_updated_code, cleanup_extracted_code

import sys
from github import Github
import os


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


class PoemState(BaseModel):
    repo: str
    pr_number : int
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

