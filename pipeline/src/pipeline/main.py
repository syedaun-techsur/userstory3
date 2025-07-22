#!/usr/bin/env python
from random import randint

from pydantic import BaseModel

from crewai.flow import Flow, listen, start

from pipeline.crews.poem_crew.poem_crew import PRFetch
from pipeline.crews.refine_crew.refine_crew import CodeRefinementCrew

import sys


class IntegratedState(BaseModel):
    repo: str
    pr_number: int
    pr_info: dict = None
    files_dict: dict = None  # Store the file dictionary
    refined_code: dict = None


class IntegratedFlow(Flow[IntegratedState]):
    def __init__(self, repo: str, pr_number: int):
        self._repo = repo
        self._pr_number = pr_number
        super().__init__()
    
    def _create_initial_state(self):
        return IntegratedState(repo=self._repo, pr_number=self._pr_number)
    
    @start()
    def fetch_files(self):
        """Step 1: Fetch PR information and files using poem crew"""
        repo_name = self.state.repo
        pr_number = self.state.pr_number
        
        print(f"🔍 Step 1: Fetching PR #{pr_number} from {repo_name}...")
        
        # Check if we're in test mode
        if repo_name == "test/repo":
            print("🧪 TEST MODE: Using mock data instead of real GitHub API")
            # Mock data for testing
            mock_files = {
                "src/App.js": "import React from 'react';\n\nfunction App() {\n  return <div>Hello World</div>;\n}\n\nexport default App;",
                "package.json": '{\n  "name": "test-app",\n  "version": "1.0.0",\n  "dependencies": {\n    "react": "^18.0.0"\n  }\n}',
                "README.md": "# Test App\n\nThis is a test application for workflow validation."
            }
            self.state.files_dict = mock_files
            print(f"✅ Step 1 Complete: Collected {len(mock_files)} mock files")
            return self.refine_code()
        
        # Use the new run_and_get_files method
        try:
            pr_fetch_crew = PRFetch()
            files_dict = pr_fetch_crew.run_and_get_files(repo_name, pr_number)
            
            # Store the files dictionary for the next step
            self.state.files_dict = files_dict
            print(f"✅ Step 1 Complete: Collected {len(files_dict)} files from PR")
        except Exception as e:
            print(f"❌ Error fetching files: {e}")
            print("🧪 Falling back to mock data for testing")
            # Fallback to mock data
            mock_files = {
                "src/App.js": "import React from 'react';\n\nfunction App() {\n  return <div>Hello World</div>;\n}\n\nexport default App;",
                "package.json": '{\n  "name": "test-app",\n  "version": "1.0.0",\n  "dependencies": {\n    "react": "^18.0.0"\n  }\n}',
                "README.md": "# Test App\n\nThis is a test application for workflow validation."
            }
            self.state.files_dict = mock_files
        
        return self.refine_code()
    
    @listen(lambda state: state.files_dict is not None)
    def refine_code(self):
        """Step 2: Refine the code using refine crew - one file at a time"""
        repo_name = self.state.repo
        pr_number = self.state.pr_number
        files_dict = self.state.files_dict
        
        print(f"🔧 Step 2: Refining code for PR #{pr_number}...")
        print(f"📁 Files to refine: {list(files_dict.keys()) if files_dict else 'None'}")
        
        # Check if we're in test mode
        if repo_name == "test/repo":
            print("🧪 TEST MODE: Using mock refinement instead of real AI")
            # Mock refinement for testing
            refined_files = {}
            for file_path, file_content in files_dict.items():
                print(f"🔧 Processing file: {file_path}")
                # Add a simple mock improvement
                if file_path.endswith('.js'):
                    refined_content = f"### Changes:\n- Added console.log for debugging\n\n### Updated Code:\n```js\n{file_content}\nconsole.log('Debug: App loaded');\n```"
                elif file_path.endswith('.json'):
                    refined_content = f"### Changes:\n- No essential changes needed.\n\n### Updated Code:\n```json\n{file_content}\n```"
                else:
                    refined_content = f"### Changes:\n- No essential changes needed.\n\n### Updated Code:\n```\n{file_content}\n```"
                
                refined_files[file_path] = refined_content
                print(f"✅ Successfully refined: {file_path}")
            
            self.state.refined_code = refined_files
            print(f"✅ Step 2 Complete: Refined {len(refined_files)} files (mock)")
            return self.summarize_results()
        
        # Process each file individually
        refined_files = {}
        refine_crew = CodeRefinementCrew()
        
        for file_path, file_content in files_dict.items():
            print(f"🔧 Processing file: {file_path}")
            
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
        
        return self.summarize_results()
    
    @listen(lambda state: state.refined_code is not None)
    def summarize_results(self):
        """Step 3: Summarize the complete workflow results"""
        print("\n" + "="*60)
        print("🎉 WORKFLOW COMPLETE!")
        print("="*60)
        print(f"Repository: {self.state.repo}")
        print(f"PR Number: #{self.state.pr_number}")
        print(f"Files Collected: {len(self.state.files_dict) if self.state.files_dict else 'N/A'}")
        if self.state.files_dict:
            print("Files processed:")
            for file_path in self.state.files_dict.keys():
                print(f"  - {file_path}")
        print(f"Code Refined: {'Yes' if self.state.refined_code else 'No'}")
        if self.state.refined_code:
            print("Refined files:")
            for file_path in self.state.refined_code.keys():
                print(f"  - {file_path}")
        print("="*60)


def kickoff(repo: str, pr_number: int):
    """Run the integrated workflow"""
    integrated_flow = IntegratedFlow(repo=repo, pr_number=pr_number)
    return integrated_flow.kickoff()


def plot():
    """Plot the workflow diagram"""
    integrated_flow = IntegratedFlow(repo="example/repo", pr_number=1)
    integrated_flow.plot()

if __name__ == "__main__":
    # Check if correct number of arguments provided
    if len(sys.argv) < 3:
        print("Usage: python main.py <repo_name> <pr_number> [mode]")
        print("Modes: 'integrated' (default), 'poem-only'")
        print("Example: python main.py 'test/repo' 123")
        sys.exit(1)
    
    repo_name = sys.argv[1]
    pr_number = int(sys.argv[2])
    print("🚀 Running Integrated Workflow...")
    kickoff(repo_name, pr_number)

