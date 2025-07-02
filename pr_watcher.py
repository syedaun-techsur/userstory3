import os
import time
import json
import subprocess
import re
from dotenv import load_dotenv
from datetime import datetime
from audit_logger import AuditLogger
from github_mcp_client import create_github_client
from step3_regenerate import regenerate_files
from step4_commit import commit_regenerated_files

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
AI_REFINE_TAG = "ai-refine"
CHECK_INTERVAL = 5  # seconds
PROCESSED_FILE = "json_output/processed_prs.json"

class PRWatcher:
    def __init__(self):
        self.github_client = create_github_client()
        self.audit_logger = AuditLogger()
        self.processed = self.load_processed()
    
    def load_processed(self):
        """Load processed PRs data"""
        if os.path.exists(PROCESSED_FILE):
            with open(PROCESSED_FILE, "r") as f:
                return json.load(f)
        return {}
    
    def save_processed(self, processed):
        """Save processed PRs data"""
        with open(PROCESSED_FILE, "w") as f:
            json.dump(processed, f)
    
    def get_ai_refine_comments_by_file(self, repo_name, pr_number, tag):
        """
        Get AI-refine comments organized by file, with timestamps
        Returns: Dict[file_name, List[Dict]] where each dict contains comment info and timestamp
        """
        file_comments = {}
        
        # Check issue comments
        issue_comments = self.github_client.get_pr_comments(repo_name, pr_number)
        for comment in issue_comments:
            if "error" in comment:
                print(f"Error getting issue comments: {comment['error']}")
                continue
                
            if tag in comment["body"].lower():
                # Extract file names from comment body
                file_matches = re.findall(r"`(.+?)`", comment["body"])
                
                for file_name in file_matches:
                    if file_name not in file_comments:
                        file_comments[file_name] = []
                    
                    file_comments[file_name].append({
                        "type": "issue_comment",
                        "body": comment["body"],
                        "timestamp": comment["updated_at"] or comment["created_at"],
                        "comment_id": comment["id"]
                    })
        
        # Check review comments (these are already file-specific)
        review_comments = self.github_client.get_pr_review_comments(repo_name, pr_number)
        for comment in review_comments:
            if "error" in comment:
                print(f"Error getting review comments: {comment['error']}")
                continue
                
            if tag in comment["body"].lower():
                file_name = comment["path"]
                if file_name not in file_comments:
                    file_comments[file_name] = []
                
                file_comments[file_name].append({
                    "type": "review_comment",
                    "body": comment["body"],
                    "timestamp": comment["updated_at"] or comment["created_at"],
                    "comment_id": comment["id"]
                })
        
        return file_comments
    
    def find_new_ai_refine_files(self, repo_name, pr_number, tag, processed):
        """
        Find files with new AI-refine comments that need processing
        Returns: List of (file_name, latest_comment_time) tuples
        """
        pr_id = f"{repo_name}#{pr_number}"
        file_comments = self.get_ai_refine_comments_by_file(repo_name, pr_number, tag)
        new_files = []
        
        for file_name, comments in file_comments.items():
            if not comments:
                print("No 'ai-refine' comment found.")
                continue
            
            # Get the latest comment time for this file
            latest_comment_time = max(comment["timestamp"] for comment in comments)
            
            # Check if this file has been processed before
            if pr_id not in processed:
                processed[pr_id] = {}
            
            last_processed_time = processed[pr_id].get(file_name)
            
            # If file hasn't been processed or has new comments, add to processing list
            if not last_processed_time or latest_comment_time > last_processed_time:
                new_files.append((file_name, latest_comment_time))
        
        return new_files
    
    def find_new_ai_refine_prs_all_repos(self, tag, processed):
        """Find all PRs with new AI-refine comments across all accessible repositories"""
        new_files_to_process = []
        
        repos = self.github_client.get_user_repos()
        for repo in repos:
            if "error" in repo:
                print(f"Error accessing repository {repo.get('full_name', 'unknown')}: {repo['error']}")
                continue
                
            try:
                prs = self.github_client.get_pull_requests(repo["full_name"], state="open", base="main")
                for pr in prs:
                    if "error" in pr:
                        print(f"Error getting PR {pr.get('number', 'unknown')}: {pr['error']}")
                        continue
                        
                    new_files = self.find_new_ai_refine_files(repo["full_name"], pr["number"], tag, processed)
                    if new_files:
                        for file_name, latest_time in new_files:
                            new_files_to_process.append({
                                "repo_name": repo["full_name"],
                                "pr_number": pr["number"],
                                "pr_title": pr["title"],
                                "pr_head_ref": pr["head"]["ref"],
                                "pr_base_ref": pr["base"]["ref"],
                                "file_name": file_name,
                                "latest_comment_time": latest_time
                            })
            except Exception as e:
                print(f"Error accessing repository {repo['full_name']}: {str(e)}")
                continue
        
        return new_files_to_process
    
    def run_pipeline_for_file(self, repo_name, pr_number, pr_title, pr_head_ref, pr_base_ref, file_name, tag):
        """Run the pipeline for a specific file"""
        print(f"[Watcher] Processing file {file_name} in PR #{pr_number} from {repo_name}")
        
        # Create file-specific PR info
        pr_info = {
            "repo_name": repo_name,
            "pr_number": pr_number,
            "ai_refine_tag": tag,
            "pr_title": pr_title,
            "target_file": file_name,
            "pr_branch": pr_head_ref,
            "main_branch": pr_base_ref
        }
        
        print(f"[Watcher] Running step3_regenerate for {file_name}...")
        regenerated_files = regenerate_files(pr_info)
        if regenerated_files is None:
            print(f"[Watcher] Error in step3 for {file_name}")
            return False
        print(f"[Watcher] Completed step3 for {file_name}")
        
        print(f"[Watcher] Running step4_commit for {file_name}...")
        commit_regenerated_files(pr_info, regenerated_files)
        print(f"[Watcher] Completed step4 for {file_name}")
        
        # Log the feedback cycle for audit
        self.log_feedback_cycle(repo_name, pr_number, pr_head_ref, pr_base_ref, file_name, regenerated_files)
        
        return True
    
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
    
    def handle_ai_refine_comment(self, repo_name, pr_number, pr_title):
        # You may need to fetch head_ref and base_ref here using self.github_client
        pr = self.github_client.get_pr_by_number(repo_name, pr_number)
        pr_head_ref = pr["head"]["ref"]
        pr_base_ref = pr["base"]["ref"]

        # Find files with new ai-refine comments
        processed = self.load_processed()
        new_files = self.find_new_ai_refine_files(repo_name, pr_number, AI_REFINE_TAG, processed)
        for file_name, latest_time in new_files:
            success = self.run_pipeline_for_file(
                repo_name, pr_number, pr_title, pr_head_ref, pr_base_ref, file_name, AI_REFINE_TAG
            )
            if success:
                pr_id = f"{repo_name}#{pr_number}"
                if pr_id not in processed:
                    processed[pr_id] = {}
                processed[pr_id][file_name] = latest_time
                self.save_processed(processed)
        
    def handle_new_pr(self, repo_name, pr_number, pr_title, pr_head_ref, pr_base_ref):
        """Process all files in a new PR (for ai-refine automation)"""
        print(f"[Watcher] Processing ALL files in PR #{pr_number} from {repo_name}")
        # Create PR info for all files (no filtering)
        pr_info = {
            "repo_name": repo_name,
            "pr_number": pr_number,
            "ai_refine_tag": AI_REFINE_TAG,
            "pr_title": pr_title,
            "target_file": None,  # None means process all files
            "pr_branch": pr_head_ref,
            "main_branch": pr_base_ref
        }
        print(f"[Watcher] Running step3_regenerate for ALL files...")
        regenerated_files = regenerate_files(pr_info)
        if regenerated_files is None:
            print(f"[Watcher] Error in step3 for ALL files")
            return False
        print(f"[Watcher] Completed step3 for ALL files")
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

#     def run(self):
#         """Main monitoring loop"""
#         print(f"[PR Watcher] Monitoring all accessible repos for PRs targeting main with '{AI_REFINE_TAG}'...")
#         print(f"[PR Watcher] Using file-level tracking and audit logging for compliance")
#         print(f"[PR Watcher] Using GitHub MCP for all GitHub interactions")
        
#         while True:
#             try:
#                 new_files = self.find_new_ai_refine_prs_all_repos(AI_REFINE_TAG, self.processed)
                
#                 if new_files:
#                     print(f"[PR Watcher] Found {len(new_files)} new file(s) with ai-refine comments to process")
                    
#                     for file_info in new_files:
#                         success = self.run_pipeline_for_file(
#                             file_info["repo_name"],
#                             file_info["pr_number"],
#                             file_info["pr_title"],
#                             file_info["pr_head_ref"],
#                             file_info["pr_base_ref"],
#                             file_info["file_name"],
#                             AI_REFINE_TAG
#                         )
                        
#                         if success:
#                             # Update processed tracking
#                             pr_id = f"{file_info['repo_name']}#{file_info['pr_number']}"
#                             if pr_id not in self.processed:
#                                 self.processed[pr_id] = {}
                            
#                             self.processed[pr_id][file_info["file_name"]] = file_info["latest_comment_time"]
#                             self.save_processed(self.processed)
                
#                 time.sleep(CHECK_INTERVAL)
                
#             except Exception as e:
#                 print(f"[PR Watcher] Error: {e}")
#                 time.sleep(CHECK_INTERVAL)

# if __name__ == "__main__":
#     watcher = PRWatcher()
#     watcher.run() 