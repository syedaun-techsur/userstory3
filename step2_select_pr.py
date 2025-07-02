import os
import json
from dotenv import load_dotenv
from github_mcp_client import create_github_client

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
AI_REFINE_TAG = "ai-refine"


def find_ai_refine_prs(tag):
    github_client = create_github_client()
    results = []
    
    repos = github_client.get_user_repos()
    for repo in repos:
        if "error" in repo:
            print(f"Error accessing repository {repo.get('full_name', 'unknown')}: {repo['error']}")
            continue
            
        try:
            prs = github_client.get_pull_requests(repo["full_name"], state="open")
            for pr in prs:
                if "error" in pr:
                    continue
                    
                found = False
                # Check issue comments
                issue_comments = github_client.get_pr_comments(repo["full_name"], pr["number"])
                for comment in issue_comments:
                    if "error" in comment:
                        continue
                    if tag in comment["body"].lower():
                        found = True
                        break
                
                # Check review comments
                if not found:
                    review_comments = github_client.get_pr_review_comments(repo["full_name"], pr["number"])
                    for comment in review_comments:
                        if "error" in comment:
                            continue
                        if tag in comment["body"].lower():
                            found = True
                            break
                
                if found:
                    results.append({
                        "repo_name": repo["full_name"],
                        "pr_number": pr["number"],
                        "ai_refine_tag": tag,
                        "pr_title": pr["title"],
                        "pr_branch": pr["head"]["ref"],
                        "main_branch": pr["base"]["ref"]
                    })
        except Exception as e:
            print(f"Error accessing repository {repo['full_name']}: {str(e)}")
            continue
    return results

if __name__ == "__main__":
    prs = find_ai_refine_prs(AI_REFINE_TAG)
    if not prs:
        print("No open PRs with ai-refine tag found in any accessible repository.")
        exit(0)
    print(f"Found {len(prs)} open PR(s) with ai-refine tag:")
    for idx, pr in enumerate(prs):
        print(f"[{idx}] {pr['repo_name']} PR #{pr['pr_number']}: {pr['pr_title']}")
    # For now, just select the first one (could be extended to prompt user)
    selected = prs[0]
    with open("json_output/step2_output.json", "w") as f:
        json.dump(selected, f, indent=2)
    print(f"Saved selected PR info to json_output/step2_output.json: {selected}") 