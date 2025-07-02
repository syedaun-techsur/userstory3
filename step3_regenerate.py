import re
import os
import asyncio
import json
from typing import Dict, Set, Optional
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp import StdioServerParameters
from dotenv import load_dotenv
from github import Github

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Initialize GitHub client directly
gh = Github(GITHUB_TOKEN)

# === CONFIGURATION ===
# Read PR info from step2_output.json
with open("json_output/step2_output.json", "r") as f:
    pr_info = json.load(f)
REPO_NAME = pr_info["repo_name"]
AI_REFINE_TAG = pr_info["ai_refine_tag"]
PR_NUMBER = pr_info["pr_number"]
TARGET_FILE = pr_info.get("target_file")  # New: specific file to process
MAX_CONTEXT_CHARS = 500000  # Increased significantly for GPT-4.1 (1M tokens ≈ 4M chars)

def get_pr_by_number(repo_name: str, pr_number: int):
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    return {
        "number": pr.number,
        "title": pr.title,
        "state": pr.state,
        "head": {
            "ref": pr.head.ref,
            "sha": pr.head.sha
        },
        "base": {
            "ref": pr.base.ref,
            "sha": pr.base.sha
        },
        "user": pr.user.login
    }

def collect_files_for_refinement(repo_name: str, pr_number: int, target_file: Optional[str] = None) -> Dict[str, str]:
    """
    Collect all files in the PR for refinement (ignore target_file and ai-refine comments).
    """
    print(f"[DEBUG] Starting collect_files_for_refinement for {repo_name} PR #{pr_number}")
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    file_names: Set[str] = set()

    # Always process all files in the PR
    print(f"[DEBUG] Getting PR files...")
    pr_files = pr.get_files()
    print(f"[DEBUG] Got {len(list(pr_files))} PR files")
    for file in pr_files:
        file_names.add(file.filename)

    print(f"[DEBUG] File names to process: {file_names}")
    result = {}
    for file_name in file_names:
        try:
            print(f"[DEBUG] Getting content for {file_name}...")
            content = repo.get_contents(file_name, ref=pr_info.get("pr_branch", "main"))
            # Handle both single file and list of files
            if isinstance(content, list):
                content = content[0]  # Take the first file if it's a list
            result[file_name] = content.decoded_content.decode("utf-8")
            print(f"[DEBUG] Successfully got content for {file_name}")
        except Exception as e:
            print(f"Error reading file {file_name}: {e}")
            continue

    print(f"[DEBUG] Returning {len(result)} files")
    return result

def fetch_repo_context(repo_name: str, pr_number: int, target_file: str) -> str:
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    context = ""
    total_chars = 0

    pr_files = pr.get_files()
    print(f"[DEBUG] Fetching full context for {len(list(pr_files))} files (excluding {target_file})")
    
    for file in pr_files:
        if file.filename == target_file:
            continue
        try:
            content = repo.get_contents(file.filename, ref=pr_info.get("pr_branch", "main"))
            # Handle both single file and list of files
            if isinstance(content, list):
                content = content[0]  # Take the first file if it's a list
            
            # Get the FULL file content instead of just 1000 chars
            full_content = content.decoded_content.decode("utf-8")
            file_size = len(full_content)
            
            section = f"\n// File: {file.filename} ({file_size} chars)\n{full_content}\n"
            
            # Still keep a reasonable limit to avoid overwhelming the model
            if total_chars + len(section) > MAX_CONTEXT_CHARS:
                print(f"[DEBUG] Context limit reached ({total_chars} chars). Stopping at {file.filename}")
                break
                
            context += section
            total_chars += len(section)
            print(f"[DEBUG] Added {file.filename} ({file_size} chars). Total context: {total_chars} chars")
            
        except Exception as e:
            print(f"Error reading file {file.filename}: {e}")
            continue

    print(f"[DEBUG] Final context size: {total_chars} characters")
    return context

def fetch_requirements_from_readme(repo_name: str, branch: str) -> str:
    repo = gh.get_repo(repo_name)
    try:
        contents = repo.get_contents("README.md", ref=branch)
        if contents:
            # get_contents returns a list, so we need to get the first item
            if isinstance(contents, list):
                return contents[0].decoded_content.decode("utf-8")
            else:
                return contents.decoded_content.decode("utf-8")
        else:
            return "# No README found\n\nPlease provide coding standards and requirements."
    except Exception as e:
        print(f"Error reading README.md: {e}")
        return "# No README found\n\nPlease provide coding standards and requirements."

## before this should be implemented and come as a output from step 1 and 2

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

async def regenerate_code_with_mcp(files: Dict[str, str], requirements: str, pr) -> Dict[str, Dict[str, str]]:
    regenerated = {}
    server_params = StdioServerParameters(command="python", args=["server.py"])

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                for file_name, old_code in files.items():
                    try:
                        print(f"[Step3] Processing file: {file_name}")
                        context = fetch_repo_context(REPO_NAME, PR_NUMBER, file_name)
                        prompt = compose_prompt(requirements, old_code, file_name, context)
                        
                        print(f"[Step3] Calling AI for {file_name}...")
                        print(f"[Step3] Prompt length: {len(prompt)} characters")
                        
                        # Add timeout to prevent infinite hanging
                        try:
                            result = await asyncio.wait_for(
                                session.call_tool("codegen", arguments={"prompt": prompt}),
                                timeout=300  # 5 minutes timeout
                            )
                            print(f"[Step3] AI call completed for {file_name}")
                        except asyncio.TimeoutError:
                            print(f"[Step3] TIMEOUT: AI call took longer than 5 minutes for {file_name}")
                            # Use original code as fallback
                            regenerated[file_name] = {
                                "old_code": old_code,
                                "changes": "AI call timed out after 5 minutes",
                                "updated_code": old_code
                            }
                            continue

                        # Handle MCP response content properly
                        response = ""
                        if result.content and len(result.content) > 0:
                            content_item = result.content[0]
                            # Check if it's a TextContent object
                            if hasattr(content_item, 'text') and hasattr(content_item, 'type') and content_item.type == "text":
                                response = content_item.text.strip()
                            else:
                                print(f"[Step3] Warning: Unexpected content type for {file_name}")
                                response = str(content_item)
                        else:
                            print(f"[Step3] Warning: No content in response for {file_name}")
                            response = ""
                        
                        print(f"\nRaw LLM response for {file_name}:\n{'-'*80}\n{response}\n{'-'*80}")

                        # Extract changes - look for changes both inside and outside <think> block
                        changes = ""
                        
                        # First try to find changes outside <think> block
                        # Look for changes that end before any code block starts
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

                        # Fallback: If changes section contains code blocks, extract only the text parts
                        if changes and "```" in changes:
                            print(f"⚠️ WARNING: Code blocks found in changes section for {file_name}. Attempting to clean up...")
                            # Remove code blocks from changes
                            changes = re.sub(r'```[a-zA-Z0-9]*\n[\s\S]*?```', '', changes)
                            changes = re.sub(r'\n\s*\n', '\n', changes)  # Clean up extra newlines
                            changes = changes.strip()

                        # Extract updated code - try multiple patterns
                        updated_code = ""
                        
                        # Pattern 1: Look for code specifically after "### Updated Code:" (most specific)
                        updated_code_match = re.search(r"### Updated Code:\s*\n```[a-zA-Z0-9]*\n([\s\S]*?)```", response, re.IGNORECASE)
                        if updated_code_match:
                            updated_code = updated_code_match.group(1).strip()
                        
                        # Pattern 2: If not found, look for code inside <think> block after "### Updated Code:"
                        if not updated_code:
                            think_match = re.search(r"<think>([\s\S]*?)</think>", response, re.IGNORECASE)
                            if think_match:
                                think_content = think_match.group(1)
                                # Look specifically for "### Updated Code:" inside think content
                                updated_code_match = re.search(r"### Updated Code:\s*\n```[a-zA-Z0-9]*\n([\s\S]*?)```", think_content, re.IGNORECASE)
                                if updated_code_match:
                                    updated_code = updated_code_match.group(1).strip()
                        
                        # Pattern 3: If still not found, look for any code block after "### Updated Code:" anywhere in response
                        if not updated_code:
                            # Find all occurrences of "### Updated Code:" and take the last one
                            updated_code_sections = re.findall(r"### Updated Code:\s*\n```[a-zA-Z0-9]*\n([\s\S]*?)```", response, re.IGNORECASE)
                            if updated_code_sections:
                                # Take the last occurrence (most likely the final answer)
                                updated_code = updated_code_sections[-1].strip()
                        
                        # Pattern 4: Look for code blocks that come directly after changes section (when LLM doesn't use proper heading)
                        if not updated_code:
                            # Find the end of changes section and look for code blocks after it
                            changes_end = re.search(r"### Changes:\n([\s\S]*?)(?=\n```[a-zA-Z0-9]*\n|### Updated Code:|$)", response, re.IGNORECASE)
                            if changes_end:
                                # Get everything after the changes section
                                after_changes = response[changes_end.end():]
                                # Look for code blocks in the remaining content
                                code_blocks = re.findall(r"```[a-zA-Z0-9]*\n([\s\S]*?)```", after_changes)
                                if code_blocks:
                                    updated_code = code_blocks[0].strip()  # Take the first code block after changes
                        
                        # Pattern 5: Last resort - if multiple code blocks exist, take the last one that's not the original
                        if not updated_code:
                            all_code_blocks = re.findall(r"```[a-zA-Z0-9]*\n([\s\S]*?)```", response)
                            if len(all_code_blocks) > 1:
                                # Skip the first code block (likely the original) and take the last one
                                updated_code = all_code_blocks[-1].strip()
                            elif len(all_code_blocks) == 1:
                                # Only one code block found, use it
                                updated_code = all_code_blocks[0].strip()

                        # Clean up the extracted code
                        if updated_code:
                            # Remove any leading/trailing whitespace and common prefixes
                            updated_code = re.sub(r'^[\s\n]*', '', updated_code)
                            updated_code = re.sub(r'[\s\n]*$', '', updated_code)
                            
                            # Remove diff markers and extract only the REPLACE section
                            if '<<<<<<< SEARCH' in updated_code and '>>>>>>> REPLACE' in updated_code:
                                # Extract only the REPLACE section
                                replace_match = re.search(r'=======\n(.*?)\n>>>>>>> REPLACE', updated_code, re.DOTALL)
                                if replace_match:
                                    updated_code = replace_match.group(1).strip()
                            
                            # Remove any remaining diff markers
                            updated_code = re.sub(r'<<<<<<< SEARCH.*?=======\n', '', updated_code, flags=re.DOTALL)
                            updated_code = re.sub(r'\n>>>>>>> REPLACE.*', '', updated_code, flags=re.DOTALL)
                            
                            # Clean up any remaining artifacts
                            updated_code = re.sub(r'client/src/.*?\.js\n```javascript\n', '', updated_code)
                            updated_code = re.sub(r'```\n$', '', updated_code)

                        # Fallback: if no updated code found, use original code
                        if not updated_code:
                            print(f"⚠️ WARNING: Could not extract updated code for {file_name}. Using original code.")
                            updated_code = old_code

                        regenerated[file_name] = {
                            "old_code": old_code,
                            "changes": changes,
                            "updated_code": updated_code
                        }
                        
                        print(f"[Step3] Successfully processed {file_name}")
                        
                    except Exception as e:
                        print(f"[Step3] Error processing file {file_name}: {e}")
                        # Add the file with original code as fallback
                        regenerated[file_name] = {
                            "old_code": old_code,
                            "changes": f"Error during processing: {str(e)}",
                            "updated_code": old_code
                        }
                        continue

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

if __name__ == "__main__":
    pr = get_pr_by_number(REPO_NAME, PR_NUMBER)
    if "error" in pr:
        print(f"Error loading PR #{PR_NUMBER}: {pr['error']}")
        exit(1)
        
    print(f"Loaded PR #{pr['number']}: {pr['title']}")
    print(f"Branch: {pr['head']['ref']}")
    
    if TARGET_FILE:
        print(f"Processing specific file: {TARGET_FILE}")
    else:
        print("Processing all files with ai-refine comments")

    files_for_update = collect_files_for_refinement(REPO_NAME, PR_NUMBER, TARGET_FILE)
    print(f"Files selected for refinement: {list(files_for_update.keys())}")

    if not files_for_update:
        print("No files found for refinement. Exiting.")
        exit(1)

    requirements_text = fetch_requirements_from_readme(REPO_NAME, pr['head']['ref'])
    print(f"Requirements from README.md:\n{'-'*60}\n{requirements_text}\n{'-'*60}")

    regenerated_files = asyncio.run(regenerate_code_with_mcp(files_for_update, requirements_text, pr))

    # Save results
    output_path = "json_output/regenerated_results.json"
    with open(output_path, "w") as f:
        json.dump(regenerated_files, f, indent=2)

    print(f"\nSaved regenerated output to {output_path}")