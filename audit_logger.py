import json
import os
from datetime import datetime
from typing import Dict, List, Optional

class AuditLogger:
    def __init__(self, log_file: str = "json_output/audit_log.json"):
        self.log_file = log_file
        self.audit_data = self._load_audit_data()
    
    def _load_audit_data(self) -> Dict:
        """Load existing audit data from file"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {
            "feedback_cycles": [],
            "file_tracking": {}  # Track processed files per PR
        }
    
    def _save_audit_data(self):
        """Save audit data to file"""
        with open(self.log_file, 'w') as f:
            json.dump(self.audit_data, f, indent=2, default=str)
    
    def log_feedback_cycle(self, 
                          repo_name: str,
                          pr_number: int,
                          pr_branch: str,
                          main_branch: str,
                          file_name: str,
                          old_code: str,
                          changes: str,
                          updated_code: str,
                          processing_timestamp: Optional[datetime] = None):
        """
        Log a feedback cycle for compliance tracking
        
        Args:
            repo_name: Name of the repository
            pr_number: Pull request number
            pr_branch: Branch name of the PR
            main_branch: Main branch that PR will be merged into
            file_name: Name of the file being processed
            old_code: Original code content
            changes: Changes description from AI
            updated_code: Updated code content
            processing_timestamp: When the processing occurred
        """
        if processing_timestamp is None:
            processing_timestamp = datetime.now()
        
        cycle_entry = {
            "timestamp": processing_timestamp.isoformat(),
            "repo_name": repo_name,
            "pr_number": pr_number,
            "pr_branch": pr_branch,
            "main_branch": main_branch,
            "file_name": file_name,
            "old_code": old_code,
            "changes": changes,
            "updated_code": updated_code
        }
        
        self.audit_data["feedback_cycles"].append(cycle_entry)
        self._save_audit_data()
        
        print(f"[Audit] Logged feedback cycle for {file_name} in PR #{pr_number}")
    
    def is_file_processed(self, repo_name: str, pr_number: int, file_name: str) -> bool:
        """Check if a specific file in a PR has been processed"""
        pr_key = f"{repo_name}#{pr_number}"
        if pr_key in self.audit_data["file_tracking"]:
            return file_name in self.audit_data["file_tracking"][pr_key]
        return False
    
    def mark_file_processed(self, repo_name: str, pr_number: int, file_name: str, timestamp: Optional[datetime] = None):
        """Mark a file as processed"""
        if timestamp is None:
            timestamp = datetime.now()
        
        pr_key = f"{repo_name}#{pr_number}"
        if pr_key not in self.audit_data["file_tracking"]:
            self.audit_data["file_tracking"][pr_key] = {}
        
        self.audit_data["file_tracking"][pr_key][file_name] = timestamp.isoformat()
        self._save_audit_data()
    
    def get_processed_files_for_pr(self, repo_name: str, pr_number: int) -> List[str]:
        """Get list of processed files for a specific PR"""
        pr_key = f"{repo_name}#{pr_number}"
        if pr_key in self.audit_data["file_tracking"]:
            return list(self.audit_data["file_tracking"][pr_key].keys())
        return []
    
    def get_feedback_cycles_for_file(self, repo_name: str, pr_number: int, file_name: str) -> List[Dict]:
        """Get all feedback cycles for a specific file"""
        cycles = []
        for cycle in self.audit_data["feedback_cycles"]:
            if (cycle["repo_name"] == repo_name and 
                cycle["pr_number"] == pr_number and 
                cycle["file_name"] == file_name):
                cycles.append(cycle)
        return cycles
    
    def get_audit_summary(self) -> Dict:
        """Get a summary of audit data"""
        return {
            "total_feedback_cycles": len(self.audit_data["feedback_cycles"]),
            "total_prs_tracked": len(self.audit_data["file_tracking"]),
            "total_files_processed": sum(len(files) for files in self.audit_data["file_tracking"].values()),
            "latest_cycle": self.audit_data["feedback_cycles"][-1] if self.audit_data["feedback_cycles"] else None
        } 