#!/usr/bin/env python
from random import randint

from pydantic import BaseModel

from crewai.flow import Flow, listen, start

from pipeline.crews.poem_crew.poem_crew import AiRefine
from pipeline.crews.refine_crew.refine_crew import CodeRefinementCrew

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
        print(f"�� Fetched {len(files_dict) if files_dict else 0} files")
        return files_dict
       
    
    @listen(fetch_files)
    def refine_code(self):
        """Step 2: Refine the code using refine crew - one file at a time"""
        repo_name = self.state.repo
        pr_number = self.state.pr_number
        files_dict = self.state.files_dict
        
        # Check if files_dict is None or empty
        if not files_dict:
            print("❌ No files to refine - files_dict is empty or None")
            self.state.refined_code = {}
            return {}
        
        # Process each file individually
        refined_files = {}
        refine_crew = CodeRefinementCrew()
        
        for file_path, file_content in files_dict.items():
            print(f"�� Processing file: {file_path}")
            
            # Create a single file dictionary for this file
            single_file_dict = {file_path: file_content}
            
            try:
                # Refine this single file
                refined_single_file = refine_crew.refine_files(single_file_dict)
                
                # Extract the refined content for this file
                if file_path in refined_single_file:
                    refined_files[file_path] = refined_single_file[file_path]
                    print(f"✅ Successfully refined: {file_path}")
                else:
                    # If refinement failed, keep original content
                    refined_files[file_path] = file_content
                    print(f"⚠️ Refinement failed for {file_path}, keeping original")
                    
            except Exception as e:
                print(f"❌ Error refining {file_path}: {e}")
                # Keep original content if refinement fails
                refined_files[file_path] = file_content
        
        # Store the refined code
        self.state.refined_code = refined_files
        print(f"✅ Step 2 Complete: Refined {len(refined_files)} files")
        
        # Display the refined code results
        print("\n" + "="*80)
        print("🎯 REFINED CODE RESULTS")
        print("="*80)
        
        for file_path, refined_content in refined_files.items():
            original_content = files_dict[file_path]
            print(f"\n📁 File: {file_path}")
            print("-" * 60)
            
            # Show a preview of the refined content
            if isinstance(refined_content, str):
                # If it's a string, show first 500 characters
                preview = refined_content[:500]
                if len(refined_content) > 500:
                    preview += "\n... (truncated)"
                print(f"✅ Refined Content Preview:\n{preview}")
            else:
                print(f"✅ Refined Content (type: {type(refined_content)}):\n{refined_content}")
            
            print("-" * 60)
        
        print("\n" + "="*80)
        print("🎉 REFINEMENT COMPLETE!")
        print("="*80)
        
        return refined_files


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

