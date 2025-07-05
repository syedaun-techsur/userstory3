import os
import json
from dotenv import load_dotenv
from datetime import datetime
from audit_logger import AuditLogger
from github_mcp_client import create_github_client
from step3_regenerate import regenerate_files
from step4_commit import commit_regenerated_files

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

class PRWatcher:
    def __init__(self):
        self.github_client = create_github_client()
        self.audit_logger = AuditLogger()
    

    def log_feedback_cycle(self, repo_name, pr_number, pr_head_ref, pr_base_ref, file_name, regenerated_files):
        """Log the feedback cycle for audit compliance"""
        try:
            if file_name in regenerated_files:
                file_data = regenerated_files[file_name]
                self.audit_logger.log_feedback_cycle(
                    repo_name=repo_name,
                    pr_number=pr_number,
                    pr_branch=pr_head_ref,
                    main_branch=pr_base_ref,
                    file_name=file_name,
                    old_code=file_data["old_code"],
                    changes=file_data["changes"],
                    updated_code=file_data["updated_code"],
                    processing_timestamp=datetime.now()
                )
                # Mark file as processed
                self.audit_logger.mark_file_processed(repo_name, pr_number, file_name)
        except Exception as e:
            print(f"[Watcher] Error logging feedback cycle: {e}")
    
    def handle_new_pr(self, repo_name, pr_number, pr_title, pr_head_ref, pr_base_ref):
        """Process all files in a new PR for AI code refinement"""
        print(f"[Watcher] Processing ALL files in PR #{pr_number} from {repo_name}")
        # Create PR info for all files
        pr_info = {
            "repo_name": repo_name,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "pr_branch": pr_head_ref,
            "main_branch": pr_base_ref
        }
        print(f"[Watcher] Running step3_regenerate (LLM + Local Processing) for ALL files...")
        regenerated_files = regenerate_files(pr_info)
        if regenerated_files is None:
            print(f"[Watcher] Error in step3 for ALL files")
            return False
        print(f"[Watcher] Completed step3 (LLM + Local Processing) for ALL files")
        print(f"[Watcher] Running step4_commit for ALL files...")
        commit_regenerated_files(pr_info, regenerated_files)
        print(f"[Watcher] Completed step4 for ALL files")
        # Log the feedback cycle for audit for all files
        try:
            for file_name in regenerated_files:
                self.log_feedback_cycle(repo_name, pr_number, pr_head_ref, pr_base_ref, file_name, regenerated_files)
        except Exception as e:
            print(f"[Watcher] Error logging feedback cycle for all files: {e}")
        return True 