import os
import json
from dotenv import load_dotenv
from github_mcp_client import create_github_client

def normalize_code(code):
    # Normalize line endings, strip trailing whitespace, remove leading/trailing blank lines
    return '\n'.join(
        line.rstrip()
        for line in code.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    ).strip()

# === Load credentials and config ===
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

def commit_regenerated_files(pr_info, regenerated_files):
    REPO_NAME = pr_info["repo_name"]
    PR_NUMBER = pr_info["pr_number"]
    github_client = create_github_client()

    # Get the PR to determine the base branch
    pr = github_client.get_pr_by_number(REPO_NAME, PR_NUMBER)
    if "error" in pr:
        print(f"Error getting PR: {pr['error']}")
        return
    
    BASE_BRANCH = pr["head"]["ref"]
    TARGET_BRANCH = f"ai_refined_code_{BASE_BRANCH}"

    # Ensure target branch exists
    branch_exists = github_client.check_branch_exists(REPO_NAME, TARGET_BRANCH)
    if "error" in branch_exists:
        print(f"Error checking branch: {branch_exists['error']}")
        return
        
    if not branch_exists["exists"]:
        # Get base branch info
        base_branch_info = github_client.get_branch(REPO_NAME, BASE_BRANCH)
        if "error" in base_branch_info:
            print(f"Error getting base branch: {base_branch_info['error']}")
            return
            
        base_sha = base_branch_info["commit"]["sha"]
        create_result = github_client.create_branch(REPO_NAME, TARGET_BRANCH, base_sha)
        if "error" in create_result:
            print(f"Error creating branch: {create_result['error']}")
            return
        print(f"Created branch: {TARGET_BRANCH}")

    # Iterate and push updates
    for fname, data in regenerated_files.items():
        changes = data.get('changes', '').strip()
        old_code = data.get('old_code', '')
        updated_code = data.get('updated_code', '')

        # If AI says no changes needed, always use the original code
        if "no changes needed" in changes.lower():
            updated_code = old_code

        # Only commit if normalized code is different
        if normalize_code(old_code) == normalize_code(updated_code):
            print(f"Skipping {fname}: No real changes detected.")
            continue

        print(f"Updating {fname} in branch '{TARGET_BRANCH}'")

        try:
            # Check if file exists on branch
            file_content = github_client.get_file_content(REPO_NAME, fname, ref=TARGET_BRANCH)
            
            # Use the AI's "changes" section in commit message
            commit_message = f"AI Refactor for {fname}:\n\nChanges:\n{data.get('changes', 'No changes described.')}"

            if "error" not in file_content:
                # File exists, update it
                result = github_client.update_file(
                    repo_name=REPO_NAME,
                    file_path=fname,
                    message=commit_message,
                    content=updated_code,
                    sha=file_content["sha"],
                    branch=TARGET_BRANCH
                )
            else:
                # File doesn't exist, create it
                result = github_client.create_file(
                    repo_name=REPO_NAME,
                    file_path=fname,
                    message=commit_message,
                    content=updated_code,
                    branch=TARGET_BRANCH
                )
            
            if result is None:
                print(f"Error updating {fname}: No response from GitHub API")
            elif "error" in result:
                print(f"Error updating {fname}: {result['error']}")
            else:
                print(f"Successfully updated {fname}")
                
        except Exception as e:
            print(f"Error processing {fname}: {e}")

    # Check if PR exists
    try:
        # Get all PRs with the target branch as head
        prs = github_client.get_pull_requests(REPO_NAME, state="open")
        existing_pr = None
        
        for pr in prs:
            if "error" in pr:
                continue
            if pr["head"]["ref"] == TARGET_BRANCH:
                existing_pr = pr
                break
        
        if not existing_pr:
            # Create new PR
            pr_result = github_client.create_pull_request(
                repo_name=REPO_NAME,
                title="AI Refactored Code Update",
                body="This PR includes updated code based on coding standards with inline changes described.",
                head=TARGET_BRANCH,
                base=BASE_BRANCH
            )
            
            if "error" in pr_result:
                print(f"Error creating PR: {pr_result['error']}")
            else:
                print(f"Created PR #{pr_result['number']}: {pr_result['title']}")
        else:
            print(f"PR already exists: #{existing_pr['number']} - {existing_pr['title']}")
            
    except Exception as e:
        print(f"Error checking/creating PR: {e}")