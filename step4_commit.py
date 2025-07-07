import os
import json
from dotenv import load_dotenv

# GitHub MCP Client
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

if not GITHUB_TOKEN:
    print("❌ GITHUB_TOKEN environment variable not set")
    exit(1)

# Initialize GitHub MCP client
github_client = create_github_client(timeout=600)  # 10 minute timeout for large files
print(f"[DEBUG] ✅ Step4 GitHub MCP client initialized")

def commit_regenerated_files(pr_info, regenerated_files):
    REPO_NAME = pr_info["repo_name"]
    PR_NUMBER = pr_info["pr_number"]
    
    print(f"[Step4] Starting commit process for {len(regenerated_files)} files")

    try:
        # Get the PR to determine the base branch
        pr = github_client.get_pr_by_number(REPO_NAME, PR_NUMBER)
        if "error" in pr:
            print(f"[Step4] Error getting PR: {pr['error']}")
            return
        
        BASE_BRANCH = pr["head"]["ref"]
        TARGET_BRANCH = f"ai_refined_code_{BASE_BRANCH}"
        
        print(f"[Step4] Base branch: {BASE_BRANCH}, Target branch: {TARGET_BRANCH}")

        # Check if target branch exists
        branch_check = github_client.check_branch_exists(REPO_NAME, TARGET_BRANCH)
        if "error" in branch_check:
            print(f"[Step4] Error checking branch: {branch_check['error']}")
            return
            
        if not branch_check["exists"]:
            # Branch doesn't exist, create it
            try:
                base_branch = github_client.get_branch(REPO_NAME, BASE_BRANCH)
                if "error" in base_branch:
                    print(f"Error getting base branch {BASE_BRANCH}: {base_branch['error']}")
                    return
                
                base_sha = base_branch["commit"]["sha"]
                create_result = github_client.create_branch(REPO_NAME, TARGET_BRANCH, base_sha)
                if "error" in create_result:
                    print(f"Error creating branch {TARGET_BRANCH}: {create_result['error']}")
                    return
                print(f"Created branch: {TARGET_BRANCH}")
            except Exception as e:
                print(f"Error creating branch {TARGET_BRANCH}: {e}")
                return
        else:
            print(f"[Step4] Target branch already exists")

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
                # Use the AI's "changes" section in commit message
                commit_message = f"AI Refactor for {fname}:\n\nChanges:\n{data.get('changes', 'No changes described.')}"

                # Check if file exists on target branch
                existing_file = github_client.get_file_content(REPO_NAME, fname, ref=TARGET_BRANCH)
                
                if "error" not in existing_file:
                    # File exists, update it
                    result = github_client.update_file(
                        repo_name=REPO_NAME,
                        file_path=fname,
                        message=commit_message,
                        content=updated_code,
                        sha=existing_file["sha"],
                        branch=TARGET_BRANCH
                    )
                    
                    if "error" in result:
                        print(f"Failed to update {fname}: {result['error']}")
                        continue
                    else:
                        print(f"Successfully updated {fname}")
                else:
                    # File doesn't exist, create it
                    result = github_client.create_file(
                        repo_name=REPO_NAME,
                        file_path=fname,
                        message=commit_message,
                        content=updated_code,
                        branch=TARGET_BRANCH
                    )
                    
                    if "error" in result:
                        print(f"Failed to create {fname}: {result['error']}")
                        continue
                    else:
                        print(f"Successfully created {fname}")
                    
            except Exception as e:
                print(f"Error processing {fname}: {e}")
                # For large files like package-lock.json, continue with other files
                if "package-lock.json" in fname:
                    print(f"[Step4] ⚠️ Large lockfile {fname} failed to upload, continuing with other files...")
                continue

        # Check if PR exists and create if needed
        try:
            # Get all open PRs to check if our target branch already has one
            prs = github_client.get_pull_requests(REPO_NAME, state="open", base=BASE_BRANCH)
            existing_pr = None
            
            for pr_item in prs:
                if pr_item["head"]["ref"] == TARGET_BRANCH:
                    existing_pr = pr_item
                    break
            
            if not existing_pr:
                # Create new PR
                new_pr = github_client.create_pull_request(
                    repo_name=REPO_NAME,
                    title="AI Refactored Code Update",
                    body="This PR includes updated code based on coding standards with inline changes described.",
                    head=TARGET_BRANCH,
                    base=BASE_BRANCH
                )
                
                if "error" in new_pr:
                    print(f"Error creating PR: {new_pr['error']}")
                else:
                    print(f"Created PR #{new_pr['number']}: {new_pr['title']}")
            else:
                print(f"PR already exists: #{existing_pr['number']} - {existing_pr['title']}")
                
        except Exception as e:
            print(f"Error checking/creating PR: {e}")
            
    except Exception as e:
        print(f"[Step4] Error in commit process: {e}")
