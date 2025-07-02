#!/usr/bin/env python3
"""
Audit Log Viewer - Utility to view and manage audit logs for compliance
"""

import json
import argparse
from datetime import datetime
from audit_logger import AuditLogger

def format_timestamp(timestamp_str):
    """Format timestamp for display"""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return timestamp_str

def view_audit_summary():
    """Display audit summary"""
    logger = AuditLogger()
    summary = logger.get_audit_summary()
    
    print("=" * 60)
    print("AUDIT LOG SUMMARY")
    print("=" * 60)
    print(f"Total Feedback Cycles: {summary['total_feedback_cycles']}")
    print(f"Total PRs Tracked: {summary['total_prs_tracked']}")
    print(f"Total Files Processed: {summary['total_files_processed']}")
    
    if summary['latest_cycle']:
        latest = summary['latest_cycle']
        print(f"\nLatest Processing:")
        print(f"  File: {latest['file_name']}")
        print(f"  PR: #{latest['pr_number']} in {latest['repo_name']}")
        print(f"  Time: {format_timestamp(latest['timestamp'])}")
    
    print("=" * 60)

def view_feedback_cycles(repo_name=None, pr_number=None, file_name=None, limit=10):
    """View feedback cycles with optional filtering"""
    logger = AuditLogger()
    
    # Load audit data
    with open(logger.log_file, 'r') as f:
        audit_data = json.load(f)
    
    cycles = audit_data.get("feedback_cycles", [])
    
    # Apply filters
    if repo_name:
        cycles = [c for c in cycles if c['repo_name'] == repo_name]
    if pr_number:
        cycles = [c for c in cycles if c['pr_number'] == pr_number]
    if file_name:
        cycles = [c for c in cycles if c['file_name'] == file_name]
    
    # Sort by timestamp (newest first)
    cycles.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Limit results
    cycles = cycles[:limit]
    
    print(f"\n{'='*80}")
    print(f"FEEDBACK CYCLES")
    if repo_name or pr_number or file_name:
        print(f"Filters: repo={repo_name}, pr={pr_number}, file={file_name}")
    print(f"{'='*80}")
    
    for i, cycle in enumerate(cycles, 1):
        print(f"\n{i}. {cycle['file_name']} in PR #{cycle['pr_number']} ({cycle['repo_name']})")
        print(f"   Time: {format_timestamp(cycle['timestamp'])}")
        print(f"   Branch: {cycle['pr_branch']} → {cycle['main_branch']}")
        print(f"   Changes: {cycle['changes'][:100]}{'...' if len(cycle['changes']) > 100 else ''}")
    
    if not cycles:
        print("No feedback cycles found matching the criteria.")

def view_file_tracking(repo_name=None, pr_number=None):
    """View file tracking information"""
    logger = AuditLogger()
    
    # Load audit data
    with open(logger.log_file, 'r') as f:
        audit_data = json.load(f)
    
    file_tracking = audit_data.get("file_tracking", {})
    
    print(f"\n{'='*60}")
    print("FILE TRACKING")
    print(f"{'='*60}")
    
    for pr_key, files in file_tracking.items():
        repo, pr_num = pr_key.split('#', 1)
        
        # Apply filters
        if repo_name and repo != repo_name:
            continue
        if pr_number and int(pr_num) != pr_number:
            continue
        
        print(f"\nPR #{pr_num} in {repo}:")
        for file_name, timestamp in files.items():
            print(f"  ✓ {file_name} (processed: {format_timestamp(timestamp)})")

def export_audit_data(output_file="json_output/audit_export.json"):
    """Export audit data to a file"""
    logger = AuditLogger()
    
    with open(logger.log_file, 'r') as f:
        audit_data = json.load(f)
    
    with open(output_file, 'w') as f:
        json.dump(audit_data, f, indent=2)
    
    print(f"Audit data exported to {output_file}")

def main():
    parser = argparse.ArgumentParser(description="Audit Log Viewer for PR Processing")
    parser.add_argument("--summary", action="store_true", help="Show audit summary")
    parser.add_argument("--cycles", action="store_true", help="Show feedback cycles")
    parser.add_argument("--tracking", action="store_true", help="Show file tracking")
    parser.add_argument("--export", metavar="FILE", help="Export audit data to file")
    parser.add_argument("--repo", help="Filter by repository name")
    parser.add_argument("--pr", type=int, help="Filter by PR number")
    parser.add_argument("--file", help="Filter by file name")
    parser.add_argument("--limit", type=int, default=10, help="Limit number of results")
    
    args = parser.parse_args()
    
    if args.summary:
        view_audit_summary()
    
    if args.cycles:
        view_feedback_cycles(args.repo, args.pr, args.file, args.limit)
    
    if args.tracking:
        view_file_tracking(args.repo, args.pr)
    
    if args.export:
        export_audit_data(args.export)
    
    # If no specific action is specified, show summary
    if not any([args.summary, args.cycles, args.tracking, args.export]):
        view_audit_summary()

if __name__ == "__main__":
    main() 