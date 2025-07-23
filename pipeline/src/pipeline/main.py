#!/usr/bin/env python
from random import randint

from pydantic import BaseModel

from crewai.flow import Flow, listen, start

from pipeline.crews.poem_crew.poem_crew import AiRefine
from pipeline.crews.refine_crew.refine_crew import CodeRefinementCrew
from pipeline.crews.local_processing_crew.local_crew import LocalProcessingCrew
from pipeline.response_extraction import extract_changes, extract_updated_code, cleanup_extracted_code

import sys


class PoemState(BaseModel):
    repo: str
    pr_number : int
    files_dict: dict = None  # Store the file dictionary
    refined_code: dict = None  # Store the refined code


class PoemFlow(Flow[PoemState]):
    def __init__(self, repo: str, pr_number: int):
        self._repo = repo
        self._pr_number = pr_number
        
        super().__init__()
    
    def _create_initial_state(self):
        return PoemState(repo=self._repo, pr_number=self._pr_number)
    
    @start()
    def fetch_files(self):
        repo_name = self.state.repo
        pr_number = self.state.pr_number

        
        files_dict = AiRefine().run_and_get_files(repo_name, pr_number)
        self.state.files_dict = files_dict
        print(f" Fetched {len(files_dict) if files_dict else 0} files")
        return files_dict
       
    
    @listen(fetch_files)
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


def kickoff(repo: str, pr_number: int):
    poem_flow = PoemFlow(repo=repo,pr_number=pr_number)
    poem_flow.kickoff()


def plot():
    poem_flow = PoemFlow()
    poem_flow.plot()


if __name__ == "__main__":
   # Check if correct number of arguments provided
    repo_name = sys.argv[1]
    pr_number = int(sys.argv[2])
    
    
    
    kickoff(repo_name, pr_number)

