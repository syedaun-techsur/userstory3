import os
import json
from dotenv import load_dotenv

# Direct GitHub API only
try:
    from github import Github
except ImportError:
    print("❌ PyGithub not installed. Install with: pip install PyGithub")
    exit(1)

def normalize_code(code):
    """Normalize line endings, strip trailing whitespace, remove leading/trailing blank lines"""
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

# Initialize direct GitHub client
github_direct = Github(GITHUB_TOKEN)
print(f"[Step4] ✅ GitHub API client initialized")

def commit_regenerated_files(pr_info, regenerated_files):
    """Commit regenerated files to GitHub using direct API calls"""
    REPO_NAME = pr_info["repo_name"]
    PR_NUMBER = pr_info["pr_number"]
    
    # Extract build validation metadata before processing files
    build_status_info = None
    if "_build_validation_metadata" in regenerated_files:
        build_metadata = regenerated_files["_build_validation_metadata"]
        try:
            build_status_info = json.loads(build_metadata.get("updated_code", "{}"))
            print(f"[Step4] 🏗️ Build validation status: {build_status_info.get('status', 'UNKNOWN')}")
        except Exception as e:
            print(f"[Step4] ⚠️ Error parsing build metadata: {e}")
        # Remove metadata from files to commit
        del regenerated_files["_build_validation_metadata"]
    
    print(f"[Step4] Starting commit process for {len(regenerated_files)} files")

    try:
        # Get the PR to determine the base branch
        repo = github_direct.get_repo(REPO_NAME)
        pr = repo.get_pull(PR_NUMBER)
        
        BASE_BRANCH = pr.head.ref  # type: ignore
        TARGET_BRANCH = f"ai_refined_code_{BASE_BRANCH}"
        
        print(f"[Step4] Base branch: {BASE_BRANCH}, Target branch: {TARGET_BRANCH}")

        # Ensure target branch exists
        try:
            target_branch = repo.get_branch(TARGET_BRANCH)
            print(f"[Step4] Target branch already exists")
        except:
            # Branch doesn't exist, create it
            try:
                base_branch = repo.get_branch(BASE_BRANCH)
                base_sha = base_branch.commit.sha
                repo.create_git_ref(ref=f"refs/heads/{TARGET_BRANCH}", sha=base_sha)
                print(f"[Step4] Created branch: {TARGET_BRANCH}")
            except Exception as e:
                print(f"[Step4] Error creating branch {TARGET_BRANCH}: {e}")
                return

        # Track successful updates and LLM corrections
        successful_updates = 0
        skipped_files = 0
        failed_files = 0
        llm_npm_corrections = 0
        llm_build_corrections = 0

        # Count LLM corrections and attempts
        for fname, data in regenerated_files.items():
            changes = data.get('changes', '')
            if 'LLM-corrected package.json' in changes or 'LLM attempted' in changes:
                llm_npm_corrections += 1
            if 'LLM-corrected build errors' in changes:
                llm_build_corrections += 1

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
                print(f"[Step4] Skipping {fname}: No real changes detected.")
                skipped_files += 1
                continue

            print(f"[Step4] Updating {fname} in branch '{TARGET_BRANCH}'")

            try:
                # Use the AI's "changes" section in commit message
                commit_message = f"AI Refactor for {fname}:\n\nChanges:\n{data.get('changes', 'No changes described.')}"

                # Check if file exists on target branch
                try:
                    existing_file = repo.get_contents(fname, ref=TARGET_BRANCH)
                    # File exists, update it
                    repo.update_file(
                        path=fname,
                        message=commit_message,
                        content=updated_code,
                        sha=existing_file.sha,
                        branch=TARGET_BRANCH
                    )
                    print(f"[Step4] ✓ Successfully updated {fname}")
                    successful_updates += 1
                except:
                    # File doesn't exist, create it
                    repo.create_file(
                        path=fname,
                        message=commit_message,
                        content=updated_code,
                        branch=TARGET_BRANCH
                    )
                    print(f"[Step4] ✓ Successfully created {fname}")
                    successful_updates += 1
                    
            except Exception as e:
                print(f"[Step4] ❌ Error processing {fname}: {e}")
                failed_files += 1
                # For large files like package-lock.json, continue with other files
                if "package-lock.json" in fname:
                    print(f"[Step4] ⚠️ Large lockfile {fname} failed to upload, continuing with other files...")
                continue

        # Print summary
        print(f"[Step4] 📊 Commit Summary: {successful_updates} updated, {skipped_files} skipped, {failed_files} failed")
        if llm_npm_corrections > 0:
            print(f"[Step4] 🤖 LLM npm Corrections: {llm_npm_corrections} package.json files auto-corrected after npm install failures")
        if llm_build_corrections > 0:
            print(f"[Step4] 🔧 LLM Build Corrections: {llm_build_corrections} source files auto-corrected after build failures")
        if build_status_info:
            build_status = build_status_info.get('status', 'UNKNOWN')
            print(f"[Step4] 🏗️ Build Status: {build_status}")
        else:
            print(f"[Step4] 🏗️ Build Status: No build validation performed")

        # Check if PR exists and create if needed
        if successful_updates > 0:
            try:
                # Get all open PRs and check if our target branch already has one
                prs = repo.get_pulls(state="open", head=f"{repo.owner.login}:{TARGET_BRANCH}")
                existing_pr = None
                
                for pr_item in prs:
                    existing_pr = pr_item
                    break
                
                if not existing_pr:
                    # Create new PR
                    pr_title = f"AI Refactored Code Update (PR #{PR_NUMBER})"
                    
                    # Build the PR body with build status if available
                    pr_body = f"""This PR includes updated code based on coding standards with inline changes described.

**Source PR:** #{PR_NUMBER}
**Files Updated:** {successful_updates}
**Files Skipped:** {skipped_files}
**Files Failed:** {failed_files}"""
                    
                    if llm_npm_corrections > 0:
                        pr_body += f"""
**🤖 LLM npm Corrections:** {llm_npm_corrections} package.json files processed with intelligent error correction (loop-based fixing until npm install succeeds)"""
                    
                    if llm_build_corrections > 0:
                        pr_body += f"""
**🔧 LLM Build Corrections:** {llm_build_corrections} source files processed with intelligent build error correction (loop-based fixing until build succeeds)"""
                    
                    # Add build validation status if available
                    if build_status_info:
                        build_status = build_status_info.get('status', 'UNKNOWN')
                        build_details = build_status_info.get('details', [])
                        
                        # Determine build status emoji
                        if build_status == "ALL BUILDS SUCCESSFUL":
                            status_emoji = "✅"
                        elif build_status == "PARTIAL SUCCESS":
                            status_emoji = "⚠️"
                        elif "FAILED" in build_status:
                            status_emoji = "❌"
                        else:
                            status_emoji = "🔧"
                        
                        pr_body += f"""

## 🏗️ Build Validation
**Status:** {status_emoji} {build_status}

**Build Details:**"""
                        for detail in build_details:
                            pr_body += f"\n- {detail}"
                        
                        if "FAILED" in build_status:
                            pr_body += f"""

⚠️ **Note:** Build validation failed, but the refined code has been pushed for review. The build issues may be due to missing configuration or can be resolved in subsequent commits."""
                    
                    pr_body += f"""

This PR was automatically generated by AI code refinement with:
- 🎯 Dynamic context updating (later files see refined earlier files)
- 📦 Dependency-optimized processing (package.json processed last)
- 🔄 Loop-based LLM npm error correction (up to 5 attempts per package.json)
- 🔧 Loop-based LLM build error correction (up to 8 attempts per build failure)
- 🏗️ Build validation with intelligent error correction"""
                    
                    new_pr = repo.create_pull(
                        title=pr_title,
                        body=pr_body,
                        head=TARGET_BRANCH,
                        base=BASE_BRANCH
                    )
                    print(f"[Step4] ✓ Created PR #{new_pr.number}: {new_pr.title}")
                else:
                    print(f"[Step4] ✓ PR already exists: #{existing_pr.number} - {existing_pr.title}")
                    
            except Exception as e:
                print(f"[Step4] ❌ Error checking/creating PR: {e}")
        else:
            print(f"[Step4] ⚠️ No files were successfully updated, skipping PR creation")
                
    except Exception as e:
        print(f"[Step4] ❌ Error in commit process: {e}")
