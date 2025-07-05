import re
import os
import asyncio
import json
import subprocess
from typing import Dict, Set, Optional
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp import StdioServerParameters
from dotenv import load_dotenv
from github_mcp_client import create_github_client
try:
    from git import Repo  # type: ignore
except ImportError:
    print("GitPython not installed. Run: pip install GitPython")
    Repo = None

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Initialize GitHub MCP client
github_client = create_github_client()

MAX_CONTEXT_CHARS = 4000000  # GPT-4.1 Mini has 1M+ token context window (1M tokens ≈ 4M chars)

def get_pr_by_number(repo_name: str, pr_number: int):
    return github_client.get_pr_by_number(repo_name, pr_number)

def collect_files_for_refinement(repo_name: str, pr_number: int, pr_info=None) -> Dict[str, str]:
    """
    Collect all files in the PR for refinement.
    """
    print(f"[DEBUG] Starting collect_files_for_refinement for {repo_name} PR #{pr_number}")
    
    # Get PR files through MCP
    print(f"[DEBUG] Getting PR files...")
    pr_files = github_client.get_pr_files(repo_name, pr_number)
    if isinstance(pr_files, dict) and pr_files.get("error"):
        print(f"Error getting PR files: {pr_files.get('error', 'Unknown error')}")
        return {}
    
    print(f"[DEBUG] Got {len(pr_files)} PR files")
    file_names: Set[str] = set()
    
    for file in pr_files:
        # Skip lock files in any directory
        if file["filename"].endswith("package-lock.json") or file["filename"].endswith("package.lock.json"):
            print(f"[DEBUG] Skipping lock file: {file['filename']}")
            continue
        # Skip GitHub workflow and config files
        if file["filename"].startswith('.github/'):
            print(f"[DEBUG] Skipping GitHub workflow or config file: {file['filename']}")
            continue
        file_names.add(file["filename"])

    print(f"[DEBUG] File names to process: {file_names}")
    result = {}
    ref = pr_info.get("pr_branch", "main") if pr_info else "main"
    
    for file_name in file_names:
        try:
            print(f"[DEBUG] Getting content for {file_name}...")
            content = github_client.get_file_content(repo_name, file_name, ref=ref)
            if "error" not in content:
                result[file_name] = content["content"]
                print(f"[DEBUG] Successfully got content for {file_name}")
            else:
                print(f"Error reading file {file_name}: {content['error']}")
        except Exception as e:
            print(f"Error reading file {file_name}: {e}")
            continue

    print(f"[DEBUG] Returning {len(result)} files")
    return result

def fetch_repo_context(repo_name: str, pr_number: int, target_file: str, pr_info=None) -> str:
    context = ""
    total_chars = 0

    # Get PR files through MCP
    pr_files = github_client.get_pr_files(repo_name, pr_number)
    if isinstance(pr_files, dict) and pr_files.get("error"):
        print(f"Error getting PR files: {pr_files.get('error', 'Unknown error')}")
        return ""
    
    ref = pr_info.get("pr_branch", "main") if pr_info else "main"
    
    for file in pr_files:
        if file["filename"] == target_file:
            continue
        # Skip lock files in any directory
        if file["filename"].endswith("package-lock.json") or file["filename"].endswith("package.lock.json"):
            continue
        # Skip GitHub workflow and config files
        if file["filename"].startswith('.github/'):
            continue
        try:
            content = github_client.get_file_content(repo_name, file["filename"], ref=ref)
            if "error" in content:
                print(f"Error reading file {file['filename']}: {content['error']}")
                continue
            
            # Get the FULL file content
            full_content = content["content"]
            file_size = len(full_content)
            
            section = f"\n// File: {file['filename']} ({file_size} chars)\n{full_content}\n"
            
            # Still keep a reasonable limit to avoid overwhelming the model
            if total_chars + len(section) > MAX_CONTEXT_CHARS:
                break
                
            context += section
            total_chars += len(section)
            
        except Exception as e:
            print(f"Error reading file {file['filename']}: {e}")
            continue
    return context

def fetch_requirements_from_readme(repo_name: str, branch: str) -> str:
    try:
        content = github_client.get_file_content(repo_name, "README.md", ref=branch)
        if "error" not in content:
            return content["content"]
        else:
            print(f"Error reading README.md: {content['error']}")
            return "# No README found\n\nPlease provide coding standards and requirements."
    except Exception as e:
        print(f"Error reading README.md: {e}")
        return "# No README found\n\nPlease provide coding standards and requirements."

def compose_prompt(requirements: str, code: str, file_name: str, context: str) -> str:
    # Get the file extension for the AI to understand the language
    file_extension = file_name.split('.')[-1].lower()
    
    return (
        f"You are an expert AI code reviewer. Your job is to improve and refactor ONLY the given file `{file_name}` "
        f"so that it meets the following coding standards:\n\n"
        f"{requirements}\n\n"
        f"---\n Repository Context (other files for reference):\n{context}\n"
        f"---\n Current Code ({file_name} - {file_extension} file):\n```{file_extension}\n{code}\n```\n"
        f"\n---\nPlease return the updated code and changes in the following EXACT format:\n"
        f"### Changes:\n- A bullet-point summary of what was changed.\n\n"
        f"### Updated Code:\n```{file_extension}\n<ONLY THE NEW/IMPROVED CODE HERE>\n```\n\n"
        f"⚠️ CRITICAL REQUIREMENTS:\n"
        f"1. Do NOT use <think> tags or any other XML-like tags\n"
        f"2. Do NOT include any reasoning or explanation outside the ### Changes section\n"
        f"3. Provide bullet-point summary of changes under the `### Changes` heading\n"
        f"4. Provide ONLY ONE code block under the `### Updated Code` heading.\n"
        f"5. Do NOT show the old code again in your response\n"
        f"6. Do NOT suggest creating new files. Only update this file\n"
        f"7. Avoid placeholder imports or components\n"
        f"8. The response must start with `### Changes:` and end with the code block\n"
        f"9. Return ONLY the improved/refactored code, not the original code\n"
        f"10. IMPORTANT: The ### Changes section must end BEFORE the ### Updated Code section\n"
        f"11. Do NOT put any code blocks directly after ### Changes without ### Updated Code heading\n"
        f"12. CRITICAL: Return the SAME TYPE of code as the original file ({file_extension})\n"
        f"13. Do NOT convert file types (e.g., don't convert .js to .css or vice versa)\n"
        f"14. Maintain the original file structure and language\n"
        f"15. If the code already meets all requirements and no improvements are needed:\n"
        f"    - In the ### Changes section, write: 'No changes needed.'\n"
        f"    - In the ### Updated Code section, return the original code unchanged.\n"
    )

def parse_token_usage(result) -> tuple[int, int, int]:
    """Parse token usage from MCP response and return (prompt_tokens, completion_tokens, total_tokens)"""
    if not (result.content and len(result.content) > 1):
        return 0, 0, 0
    
    token_usage_item = result.content[1]
    if not (hasattr(token_usage_item, 'type') and token_usage_item.type == 'text' and hasattr(token_usage_item, 'text')):
        return 0, 0, 0
    
    usage_str = token_usage_item.text.replace("Token usage: ", "").strip()
    if not usage_str or usage_str == "unavailable":
        return 0, 0, 0
    
    try:
        # Extract numbers using regex for CompletionUsage object
        prompt_match = re.search(r'prompt_tokens=(\d+)', usage_str)
        completion_match = re.search(r'completion_tokens=(\d+)', usage_str)
        total_match = re.search(r'total_tokens=(\d+)', usage_str)
        
        prompt_tokens = int(prompt_match.group(1)) if prompt_match else 0
        completion_tokens = int(completion_match.group(1)) if completion_match else 0
        total_tokens = int(total_match.group(1)) if total_match else 0
        
        return prompt_tokens, completion_tokens, total_tokens
    except Exception:
        return 0, 0, 0

def extract_response_content(result, file_name: str) -> str:
    """Extract text content from MCP response"""
    if not (result.content and len(result.content) > 0):
        print(f"[Step3] Warning: No content in response for {file_name}")
        return ""
    
    content_item = result.content[0]
    if hasattr(content_item, 'text') and hasattr(content_item, 'type') and content_item.type == "text":
        return content_item.text.strip()
    else:
        print(f"[Step3] Warning: Unexpected content type for {file_name}")
        return str(content_item)

def extract_changes(response: str, file_name: str) -> str:
    """Extract changes section from AI response"""
    changes = ""
    
    # First try to find changes outside <think> block
    changes_match = re.search(r"### Changes:\n([\s\S]*?)(?=\n```[a-zA-Z0-9]*\n|### Updated Code:|$)", response, re.IGNORECASE)
    if changes_match:
        changes = changes_match.group(1).strip()
    else:
        # If not found outside, look inside <think> block
        think_match = re.search(r"<think>([\s\S]*?)</think>", response, re.IGNORECASE)
        if think_match:
            think_content = think_match.group(1)
            changes_match = re.search(r"### Changes:\n([\s\S]*?)(?=\n```[a-zA-Z0-9]*\n|### Updated Code:|$)", think_content, re.IGNORECASE)
            if changes_match:
                changes = changes_match.group(1).strip()
    
    # Clean up if changes section contains code blocks
    if changes and "```" in changes:
        print(f"⚠️ WARNING: Code blocks found in changes section for {file_name}. Attempting to clean up...")
        changes = re.sub(r'```[a-zA-Z0-9]*\n[\s\S]*?```', '', changes)
        changes = re.sub(r'\n\s*\n', '\n', changes)  # Clean up extra newlines
        changes = changes.strip()
    
    return changes

def extract_updated_code(response: str) -> str:
    """Extract updated code from AI response using multiple fallback patterns"""
    # Pattern 1: Look for code specifically after "### Updated Code:" (most specific)
    updated_code_match = re.search(r"### Updated Code:\s*\n```[a-zA-Z0-9]*\n([\s\S]*?)```", response, re.IGNORECASE)
    if updated_code_match:
        return updated_code_match.group(1).strip()
    
    # Pattern 2: If not found, look for code inside <think> block after "### Updated Code:"
    think_match = re.search(r"<think>([\s\S]*?)</think>", response, re.IGNORECASE)
    if think_match:
        think_content = think_match.group(1)
        updated_code_match = re.search(r"### Updated Code:\s*\n```[a-zA-Z0-9]*\n([\s\S]*?)```", think_content, re.IGNORECASE)
        if updated_code_match:
            return updated_code_match.group(1).strip()
    
    # Pattern 3: If still not found, look for any code block after "### Updated Code:" anywhere in response
    updated_code_sections = re.findall(r"### Updated Code:\s*\n```[a-zA-Z0-9]*\n([\s\S]*?)```", response, re.IGNORECASE)
    if updated_code_sections:
        return updated_code_sections[-1].strip()  # Take the last occurrence
    
    # Pattern 4: Look for code blocks that come directly after changes section
    changes_end = re.search(r"### Changes:\n([\s\S]*?)(?=\n```[a-zA-Z0-9]*\n|### Updated Code:|$)", response, re.IGNORECASE)
    if changes_end:
        after_changes = response[changes_end.end():]
        code_blocks = re.findall(r"```[a-zA-Z0-9]*\n([\s\S]*?)```", after_changes)
        if code_blocks:
            return code_blocks[0].strip()
    
    # Pattern 5: Last resort - if multiple code blocks exist, take the last one
    all_code_blocks = re.findall(r"```[a-zA-Z0-9]*\n([\s\S]*?)```", response)
    if len(all_code_blocks) > 1:
        return all_code_blocks[-1].strip()
    elif len(all_code_blocks) == 1:
        return all_code_blocks[0].strip()
    
    return ""

def cleanup_extracted_code(updated_code: str) -> str:
    """Clean up extracted code by removing unwanted artifacts"""
    if not updated_code:
        return updated_code
    
    # Remove any leading/trailing whitespace
    updated_code = re.sub(r'^[\s\n]*', '', updated_code)
    updated_code = re.sub(r'[\s\n]*$', '', updated_code)
    
    # Remove diff markers and extract only the REPLACE section
    if '<<<<<<< SEARCH' in updated_code and '>>>>>>> REPLACE' in updated_code:
        replace_match = re.search(r'=======\n(.*?)\n>>>>>>> REPLACE', updated_code, re.DOTALL)
        if replace_match:
            updated_code = replace_match.group(1).strip()
    
    # Remove any remaining diff markers
    updated_code = re.sub(r'<<<<<<< SEARCH.*?=======\n', '', updated_code, flags=re.DOTALL)
    updated_code = re.sub(r'\n>>>>>>> REPLACE.*', '', updated_code, flags=re.DOTALL)
    
    # Clean up any remaining artifacts
    updated_code = re.sub(r'client/src/.*?\.js\n```javascript\n', '', updated_code)
    updated_code = re.sub(r'```\n$', '', updated_code)
    
    return updated_code

async def process_single_file(session, file_name: str, old_code: str, requirements: str, pr_info: Optional[dict] = None) -> dict:
    """Process a single file through the AI refinement pipeline"""
    try:
        print(f"[Step3] Processing file: {file_name}")
        
        # Fetch context and compose prompt
        if pr_info is None:
            raise ValueError("pr_info cannot be None")
        repo_name = pr_info["repo_name"]
        pr_number = pr_info["pr_number"]
        context = fetch_repo_context(repo_name, pr_number, file_name, pr_info)
        prompt = compose_prompt(requirements, old_code, file_name, context)
        
        print(f"[Step3] Calling AI for {file_name}...")
        
        # Call AI with timeout
        try:
            result = await asyncio.wait_for(
                session.call_tool("codegen", arguments={"prompt": prompt}),
                timeout=300  # 5 minutes timeout
            )
            print(f"[Step3] AI call completed for {file_name}")
        except asyncio.TimeoutError:
            print(f"[Step3] TIMEOUT: AI call took longer than 5 minutes for {file_name}")
            return {
                "old_code": old_code,
                "changes": "AI call timed out after 5 minutes",
                "updated_code": old_code,
                "token_usage": (0, 0, 0)
            }
        
        # Extract response content
        response = extract_response_content(result, file_name)
        
        # Parse token usage
        token_usage = parse_token_usage(result)
        
        # Extract changes and updated code
        changes = extract_changes(response, file_name)
        updated_code = extract_updated_code(response)
        updated_code = cleanup_extracted_code(updated_code)
        
        # Fallback if no updated code found
        if not updated_code:
            print(f"⚠️ WARNING: Could not extract updated code for {file_name}. Using original code.")
            updated_code = old_code
        
        print(f"[Step3] Successfully processed {file_name}")
        
        return {
            "old_code": old_code,
            "changes": changes,
            "updated_code": updated_code,
            "token_usage": token_usage
        }
        
    except Exception as e:
        print(f"[Step3] Error processing file {file_name}: {e}")
        return {
            "old_code": old_code,
            "changes": f"Error during processing: {str(e)}",
            "updated_code": old_code,
            "token_usage": (0, 0, 0)
        }

async def regenerate_code_with_mcp(files: Dict[str, str], requirements: str, pr, pr_info=None) -> Dict[str, Dict[str, str]]:
    """Main function to regenerate code using MCP - now much cleaner and focused"""
    regenerated = {}
    server_params = StdioServerParameters(command="python", args=["server.py"])

    # Accumulate total token usage
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                # Process each file
                for file_name, old_code in files.items():
                    file_result = await process_single_file(session, file_name, old_code, requirements, pr_info)
                    
                    # Extract token usage and accumulate
                    prompt_tokens, completion_tokens, tokens = file_result.pop("token_usage", (0, 0, 0))
                    total_prompt_tokens += prompt_tokens
                    total_completion_tokens += completion_tokens
                    total_tokens += tokens
                    
                    # Store the result
                    regenerated[file_name] = file_result

        # Print final token usage and pricing
        print(f"[Step3] TOTAL TOKEN USAGE: prompt_tokens={total_prompt_tokens}, completion_tokens={total_completion_tokens}, total_tokens={total_tokens}")
        
        # Calculate and print total API price for OpenAI GPT-4.1 Mini
        input_price = (total_prompt_tokens / 1000) * 0.00042
        output_price = (total_completion_tokens / 1000) * 0.00168
        total_price = input_price + output_price
        print(f"[Step3] OpenAI GPT-4.1 Mini API PRICING: Total=${total_price:.4f} (input=${input_price:.4f}, output=${output_price:.4f})")

    except Exception as e:
        print(f"[Step3] Error with MCP client: {e}")
        # If MCP fails, add all files with original code as fallback
        for file_name, old_code in files.items():
            regenerated[file_name] = {
                "old_code": old_code,
                "changes": f"MCP client error: {str(e)}",
                "updated_code": old_code
            }

    return regenerated

def get_persistent_workspace(repo_name, pr_branch, pr_number):
    """Get or create persistent workspace for this PR"""
    # Create workspace directory
    workspace_base = "workspace"
    os.makedirs(workspace_base, exist_ok=True)
    
    # Use repo name and PR number for unique workspace
    safe_repo_name = repo_name.replace('/', '_').replace('\\', '_')
    workspace_dir = os.path.join(workspace_base, f"{safe_repo_name}_PR{pr_number}")
    
    return workspace_dir

def process_pr_with_local_repo(pr_info, regenerated_files):
    """Clone PR branch, apply LLM changes, generate lockfile, and prepare for test generation"""
    
    if not regenerated_files:
        print("[LocalRepo] No files to process")
        return regenerated_files
    
    if Repo is None:
        print("[LocalRepo] ❌ GitPython not available. Skipping local repo processing.")
        print("[LocalRepo] Install with: pip install GitPython")
        return regenerated_files
    
    REPO_NAME = pr_info["repo_name"]
    PR_BRANCH = pr_info["pr_branch"]
    PR_NUMBER = pr_info["pr_number"]
    
    print(f"[LocalRepo] Starting local processing for {REPO_NAME} branch {PR_BRANCH}")
    
    try:
        # Get persistent workspace for this PR
        workspace_dir = get_persistent_workspace(REPO_NAME, PR_BRANCH, PR_NUMBER)
        
        # Clone or update the repository
        if os.path.exists(workspace_dir):
            print(f"[LocalRepo] Updating existing workspace: {workspace_dir}")
            try:
                repo = Repo(workspace_dir)
                repo.remotes.origin.pull()
            except Exception as e:
                print(f"[LocalRepo] Error updating workspace, recreating: {e}")
                import shutil
                shutil.rmtree(workspace_dir)
                repo_url = f"https://{GITHUB_TOKEN}@github.com/{REPO_NAME}.git"
                repo = Repo.clone_from(repo_url, workspace_dir, branch=PR_BRANCH)
        else:
            print(f"[LocalRepo] Creating new workspace: {workspace_dir}")
            repo_url = f"https://{GITHUB_TOKEN}@github.com/{REPO_NAME}.git"
            repo = Repo.clone_from(repo_url, workspace_dir, branch=PR_BRANCH)
        
        repo_path = workspace_dir
        
        # Apply all LLM changes to local files
        print(f"[LocalRepo] Applying LLM changes to {len(regenerated_files)} files...")
        
        for file_path, file_data in regenerated_files.items():
            local_file_path = os.path.join(repo_path, file_path)
            
            # Create directories if they don't exist
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
            
            # Write the LLM-refined content to the local file
            with open(local_file_path, "w", encoding="utf-8") as f:
                f.write(file_data["updated_code"])
            
            print(f"[LocalRepo] ✓ Applied LLM changes to {file_path}")
        
        # Generate lockfile if package.json was changed
        if "package.json" in regenerated_files:
            print("[LocalRepo] package.json detected, running npm install to generate lockfile...")
            
            try:
                # Run npm install
                result = subprocess.run(
                    ["npm", "install"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minute timeout
                )
                
                if result.returncode == 0:
                    print("[LocalRepo] ✓ npm install completed successfully")
                    
                    # Read the newly generated package-lock.json
                    lockfile_path = os.path.join(repo_path, "package-lock.json")
                    if os.path.exists(lockfile_path):
                        with open(lockfile_path, "r", encoding="utf-8") as f:
                            lockfile_content = f.read()
                        
                        # Add the lockfile to regenerated_files for GitHub API push
                        regenerated_files["package-lock.json"] = {
                            "old_code": "",  # Could fetch existing lockfile from GitHub if needed
                            "changes": "Regenerated lockfile after package.json update via npm install",
                            "updated_code": lockfile_content
                        }
                        print("[LocalRepo] ✓ Generated and added package-lock.json to commit")
                    else:
                        print("[LocalRepo] ⚠️ Warning: package-lock.json not generated by npm install")
                else:
                    print(f"[LocalRepo] ❌ npm install failed with return code {result.returncode}")
                    print(f"[LocalRepo] stdout: {result.stdout}")
                    print(f"[LocalRepo] stderr: {result.stderr}")
            
            except subprocess.TimeoutExpired:
                print("[LocalRepo] ❌ npm install timed out after 5 minutes")
            except FileNotFoundError:
                print("[LocalRepo] ❌ npm not found. Please ensure Node.js and npm are installed.")
            except Exception as e:
                print(f"[LocalRepo] ❌ Error during npm install: {e}")
        
        # TODO: Future user story - Generate and run tests here
        # print("[LocalRepo] Preparing for test generation and execution...")
        # test_files = generate_test_cases(repo_path, regenerated_files)
        # regenerated_files.update(test_files)
        # 
        # # Run tests with Jest, Selenium, Cucumber
        # run_jest_tests(repo_path)
        # run_selenium_tests(repo_path)
        # run_cucumber_tests(repo_path)
        
        print(f"[LocalRepo] ✓ Local processing completed. Final file count: {len(regenerated_files)}")
        print(f"[LocalRepo] ✓ Workspace preserved at: {workspace_dir}")
        
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during local repo processing: {e}")
        print("[LocalRepo] Continuing with original files...")
    
    return regenerated_files


def regenerate_files(pr_info):
    REPO_NAME = pr_info["repo_name"]
    PR_NUMBER = pr_info["pr_number"]
    
    pr = get_pr_by_number(REPO_NAME, PR_NUMBER)
    if "error" in pr:
        print(f"Error loading PR #{PR_NUMBER}: {pr['error']}")
        return None
    
    print(f"Loaded PR #{pr['number']}: {pr['title']}")
    print(f"Branch: {pr['head']['ref']}")
    print("Processing all files in the PR")

    files_for_update = collect_files_for_refinement(REPO_NAME, PR_NUMBER, pr_info)
    print(f"Files selected for refinement: {list(files_for_update.keys())}")

    if not files_for_update:
        print("No files found for refinement. Exiting.")
        return None

    requirements_text = fetch_requirements_from_readme(REPO_NAME, pr['head']['ref'])
    print(f"Requirements from README.md:\n{'-'*60}\n{requirements_text}\n{'-'*60}")

    regenerated_files = asyncio.run(regenerate_code_with_mcp(files_for_update, requirements_text, pr, pr_info))
    
    # Process files locally (clone repo, apply changes, generate lockfile)
    regenerated_files = process_pr_with_local_repo(pr_info, regenerated_files)
    
    return regenerated_files