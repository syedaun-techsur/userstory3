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

from response_extraction import (
    extract_response_content, extract_changes, extract_updated_code, cleanup_extracted_code
)

from extracting_dependencies import (
    extract_external_dependencies, parse_file_dependencies, convert_commonjs_to_es_modules
)

try:
    from git import Repo  # type: ignore
except ImportError:
    print("GitPython not installed. Run: pip install GitPython")
    Repo = None

# Direct GitHub API (primary method)
try:
    from github import Github
except ImportError:
    print("❌ PyGithub not installed. Install with: pip install PyGithub")
    exit(1)

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not GITHUB_TOKEN:
    print("❌ GITHUB_TOKEN environment variable not set")
    exit(1)

# Initialize direct GitHub client
github_direct = Github(GITHUB_TOKEN)
print(f"[DEBUG] ✅ GitHub API client initialized")

MAX_CONTEXT_CHARS = 4000000  # GPT-4.1 Mini has 1M+ token context window (1M tokens ≈ 4M chars)

# External dependency tracking for package.json optimization
EXTERNAL_DEPENDENCY_STORE = {}

def update_external_dependency_store(file_path: str, external_deps: Set[str]):
    """Update the global external dependency store with dependencies from a file"""
    if external_deps:
        EXTERNAL_DEPENDENCY_STORE[file_path] = external_deps
        print(f"[Step3] 📦 Tracked {len(external_deps)} external dependencies from {file_path}: {sorted(external_deps)}")
    else:
        print(f"[Step3] 📦 No external dependencies found in {file_path}")

def get_dependency_summary_for_package_json() -> str:
    """
    Generate a lightweight dependency summary for package.json files.
    Returns a summary of all external dependencies used across all files.
    """
    if not EXTERNAL_DEPENDENCY_STORE:
        return "No external dependencies found in project files."
    
    # Aggregate all external dependencies
    all_deps = set()
    deps_by_file = {}
    
    for file_path, deps in EXTERNAL_DEPENDENCY_STORE.items():
        all_deps.update(deps)
        if deps:  # Only include files that have dependencies
            deps_by_file[file_path] = sorted(deps)
    
    # Create summary
    summary = f"""
EXTERNAL DEPENDENCY SUMMARY
===========================

Total unique external packages used: {len(all_deps)}

ALL EXTERNAL DEPENDENCIES:
{', '.join(sorted(all_deps))}

DEPENDENCIES BY FILE:
"""
    
    for file_path, deps in deps_by_file.items():
        summary += f"\n{file_path}:\n  - {', '.join(deps)}\n"
    
    summary += f"""
DEPENDENCY ANALYSIS GUIDANCE:
- Ensure all packages above are properly declared in package.json
- Check for version conflicts between dependencies
- Consider if any dependencies are missing or unused
- Review if development dependencies are correctly categorized
"""
    
    return summary

def get_pr_by_number(repo_name: str, pr_number: int):
    """Get PR by number using direct GitHub API"""
    print(f"[DEBUG] Getting PR #{pr_number} from {repo_name} using direct GitHub API...")
    
    if not github_direct:
        return {"error": "GitHub API not available"}
    
    try:
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        result = {
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "head": {
                "ref": pr.head.ref if pr.head else None,  # type: ignore
                "sha": pr.head.sha if pr.head else None   # type: ignore
            },
            "base": {
                "ref": pr.base.ref if pr.base else None,  # type: ignore
                "sha": pr.base.sha if pr.base else None   # type: ignore
            },
            "user": pr.user.login if pr.user else None,
            "created_at": pr.created_at.isoformat() if pr.created_at else None,
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
            "mergeable": pr.mergeable,
            "mergeable_state": pr.mergeable_state
        }
        print(f"[DEBUG] Successfully got PR #{pr.number}: {pr.title}")
        return result
    except Exception as e:
        print(f"[DEBUG] Error getting PR: {e}")
        return {"error": str(e)}

def collect_files_for_refinement(repo_name: str, pr_number: int, pr_info=None) -> Dict[str, str]:
    """
    Collect all files in the PR for refinement using direct GitHub API.
    """
    print(f"[DEBUG] Starting collect_files_for_refinement for {repo_name} PR #{pr_number}")
    print(f"[DEBUG] Using direct GitHub API...")
    
    if not github_direct:
        print(f"[DEBUG] Direct GitHub API not available")
        return {}
    
    try:
        # Get PR using direct GitHub API
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        
        print(f"[DEBUG] Got PR #{pr.number}: {pr.title}")
        
        # Get PR files
        pr_files = []
        for file in pr.get_files():
            pr_files.append({
                "filename": file.filename,
                "status": file.status,
                "additions": file.additions,
                "deletions": file.deletions,
                "changes": file.changes
            })
            
        print(f"[DEBUG] Got {len(pr_files)} PR files")
        
        # Filter files
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
            # Skip asset and binary files (don't need AI refinement)
            asset_extensions = [
                # Images
                '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
                # Videos
                '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
                # Audio
                '.mp3', '.wav', '.flac', '.aac', '.ogg',
                # Fonts
                '.ttf', '.otf', '.woff', '.woff2', '.eot',
                # Documents
                '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                # Archives
                '.zip', '.rar', '.7z', '.tar', '.gz',
                # Binaries
                '.exe', '.dll', '.so', '.dylib'
            ]
            if any(file["filename"].lower().endswith(ext) for ext in asset_extensions):
                print(f"[DEBUG] Skipping asset/binary file: {file['filename']}")
                continue
            file_names.add(file["filename"])

        print(f"[DEBUG] File names to process: {file_names}")
        
        # Get file contents
        result = {}
        ref = pr_info.get("pr_branch", pr.head.ref) if pr_info else pr.head.ref  # type: ignore
        
        for file_name in file_names:
            try:
                print(f"[DEBUG] Getting content for {file_name}...")
                # Get file content from the PR branch
                file_content = repo.get_contents(file_name, ref=ref)
                
                # Handle both single file and list of files
                if isinstance(file_content, list):
                    # If it's a directory, skip it
                    print(f"[DEBUG] Skipping directory {file_name}")
                    continue
                else:
                    # Single file
                    content = file_content.decoded_content.decode('utf-8')
                    result[file_name] = content
                    print(f"[DEBUG] Successfully got content for {file_name}")
            except Exception as e:
                print(f"[DEBUG] Error reading file {file_name}: {e}")
                continue

        print(f"[DEBUG] Returning {len(result)} files")
        return result
        
    except Exception as e:
        print(f"[DEBUG] Direct API failed: {e}")
        return {}

def fetch_repo_context(repo_name: str, pr_number: int, target_file: str, pr_info=None) -> str:
    """Legacy function - use fetch_dynamic_context instead for updated context"""
    context = ""
    total_chars = 0

    if not github_direct:
        print(f"[DEBUG] Direct GitHub API not available for context")
        return ""

    try:
        # Get PR files through direct GitHub API
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        
        ref = pr_info.get("pr_branch", pr.head.ref) if pr_info else pr.head.ref  # type: ignore
        
        for file in pr.get_files():
            if file.filename == target_file:
                continue
            # Skip lock files in any directory
            if file.filename.endswith("package-lock.json") or file.filename.endswith("package.lock.json"):
                continue
            # Skip GitHub workflow and config files
            if file.filename.startswith('.github/'):
                continue
            # Skip asset and binary files (don't need in context)
            asset_extensions = [
                # Images
                '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
                # Videos
                '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
                # Audio
                '.mp3', '.wav', '.flac', '.aac', '.ogg',
                # Fonts
                '.ttf', '.otf', '.woff', '.woff2', '.eot',
                # Documents
                '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                # Archives
                '.zip', '.rar', '.7z', '.tar', '.gz',
                # Binaries
                '.exe', '.dll', '.so', '.dylib'
            ]
            if any(file.filename.lower().endswith(ext) for ext in asset_extensions):
                continue
            try:
                file_content = repo.get_contents(file.filename, ref=ref)
                
                # Handle both single file and list of files
                if isinstance(file_content, list):
                    continue  # Skip directories
                
                # Get the FULL file content
                full_content = file_content.decoded_content.decode('utf-8')
                file_size = len(full_content)
                
                section = f"\n// File: {file.filename} ({file_size} chars)\n{full_content}\n"
                
                # Still keep a reasonable limit to avoid overwhelming the model
                if total_chars + len(section) > MAX_CONTEXT_CHARS:
                    break
                    
                context += section
                total_chars += len(section)
                
            except Exception as e:
                print(f"Error reading file {file.filename}: {e}")
                continue
                
    except Exception as e:
        print(f"Error getting PR context: {e}")
        return ""
        
    return context

def fetch_dynamic_context(target_file: str, dynamic_context_cache: Dict[str, str], pr_files: Set[str], processed_files: Optional[Set[str]] = None) -> str:
    """
    Fetch context using dynamic cache with DEPENDENCY-BASED filtering.
    Only includes files that the target file actually imports/depends on.
    """
    context = ""
    total_chars = 0
    refined_files_count = 0
    original_files_count = 0
    
    if processed_files is None:
        processed_files = set()
    
    print(f"[Step3] 🎯 Building DEPENDENCY-OPTIMIZED context for {target_file}...")
    
    # Get the target file content to parse its dependencies
    target_content = dynamic_context_cache.get(target_file, "")
    
    # Parse dependencies from the target file
    dependencies = parse_file_dependencies(target_file, target_content, pr_files)
    
    # Special case: package.json should see DEPENDENCY SUMMARY instead of all files
    if target_file.endswith('package.json'):
        print(f"[Step3] 📦 package.json detected - using DEPENDENCY SUMMARY for lightweight analysis")
        dependency_summary = get_dependency_summary_for_package_json()
        print(f"[Step3] 📊 DEPENDENCY SUMMARY: {len(dependency_summary)} characters vs {total_chars:,} from all files")
        print(f"[Step3] 💡 Context optimization: Using dependency summary instead of all file contents")
        return dependency_summary
    elif dependencies:
        relevant_files = dependencies
        print(f"[Step3] 🔗 Using {len(relevant_files)} dependency-based context files for {target_file}")
    else:
        print(f"[Step3] 🚫 No dependencies found for {target_file} - using minimal context")
        relevant_files = set()  # No context if no dependencies
    
    # Build context from relevant files only
    for file_name in relevant_files:
        if file_name == target_file:
            continue  # Skip the target file itself
            
        # Skip lock files and GitHub config files
        if file_name.endswith("package-lock.json") or file_name.endswith("package.lock.json"):
            continue
        if file_name.startswith('.github/'):
            continue
        # Skip asset and binary files (don't need in context)
        asset_extensions = [
            # Images
            '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
            # Videos
            '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
            # Audio
            '.mp3', '.wav', '.flac', '.aac', '.ogg',
            # Fonts
            '.ttf', '.otf', '.woff', '.woff2', '.eot',
            # Documents
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            # Archives
            '.zip', '.rar', '.7z', '.tar', '.gz',
            # Binaries
            '.exe', '.dll', '.so', '.dylib'
        ]
        if any(file_name.lower().endswith(ext) for ext in asset_extensions):
            continue
            
        # Get the file content from dynamic cache
        if file_name in dynamic_context_cache:
            file_content = dynamic_context_cache[file_name]
            file_size = len(file_content)
            
            # Determine if this file is refined or original
            is_refined = file_name in processed_files
            status = "🎯 REFINED" if is_refined else "📄 ORIGINAL"
            dependency_status = "📦 ALL-CONTEXT" if target_file.endswith('package.json') else "🔗 DEPENDENCY"
            
            section = f"\n// File: {file_name} ({file_size} chars) [{status}] [{dependency_status}]\n{file_content}\n"
            
            # Keep reasonable limit to avoid overwhelming the model
            if total_chars + len(section) > MAX_CONTEXT_CHARS:
                print(f"[Step3] ⚠️ Context size limit reached ({MAX_CONTEXT_CHARS} chars), stopping context build")
                break
                
            context += section
            total_chars += len(section)
            
            if is_refined:
                refined_files_count += 1
                print(f"[Step3] ✅ Added DEPENDENCY {file_name} to context ({file_size} chars) - REFINED VERSION")
            else:
                original_files_count += 1
                print(f"[Step3] 📄 Added DEPENDENCY {file_name} to context ({file_size} chars) - ORIGINAL VERSION")
        else:
            print(f"[Step3] ⚠️ Warning: dependency {file_name} not found in dynamic cache")
    
    print(f"[Step3] 📊 DEPENDENCY-OPTIMIZED context summary for {target_file}:")
    print(f"[Step3]   - Context strategy: {'ALL FILES (package.json)' if target_file.endswith('package.json') else 'DEPENDENCIES ONLY'}")
    print(f"[Step3]   - Total chars: {total_chars:,}")
    print(f"[Step3]   - Dependencies found: {len(dependencies) if not target_file.endswith('package.json') else len(pr_files) - 1}")
    print(f"[Step3]   - Refined files in context: {refined_files_count}")
    print(f"[Step3]   - Original files in context: {original_files_count}")
    print(f"[Step3]   - Total context files: {refined_files_count + original_files_count}")
    print(f"[Step3]   - Token efficiency: {((len(pr_files) - 1) - (refined_files_count + original_files_count))} files skipped")
    
    return context

def initialize_dynamic_context_cache(repo_name: str, pr_number: int, pr_info=None) -> tuple[Dict[str, str], Set[str]]:
    """
    Initialize the dynamic context cache with original file contents from GitHub API.
    Returns (cache_dict, file_set) for dynamic processing.
    """
    print(f"[Step3] Initializing dynamic context cache...")
    cache = {}
    pr_files = set()
    
    if not github_direct:
        print(f"[Step3] GitHub API not available for cache initialization")
        return cache, pr_files

    try:
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        
        ref = pr_info.get("pr_branch", pr.head.ref) if pr_info else pr.head.ref  # type: ignore
        
        for file in pr.get_files():
            # Skip lock files and GitHub config files
            if file.filename.endswith("package-lock.json") or file.filename.endswith("package.lock.json"):
                print(f"[Step3] Skipping lock file: {file.filename}")
                continue
            if file.filename.startswith('.github/'):
                print(f"[Step3] Skipping GitHub config file: {file.filename}")
                continue
            # Skip asset and binary files (don't need in context)
            asset_extensions = [
                # Images
                '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
                # Videos
                '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
                # Audio
                '.mp3', '.wav', '.flac', '.aac', '.ogg',
                # Fonts
                '.ttf', '.otf', '.woff', '.woff2', '.eot',
                # Documents
                '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                # Archives
                '.zip', '.rar', '.7z', '.tar', '.gz',
                # Binaries
                '.exe', '.dll', '.so', '.dylib'
            ]
            if any(file.filename.lower().endswith(ext) for ext in asset_extensions):
                print(f"[Step3] Skipping asset/binary file: {file.filename}")
                continue
                
            pr_files.add(file.filename)
            
            try:
                file_content = repo.get_contents(file.filename, ref=ref)
                
                # Handle both single file and list of files
                if isinstance(file_content, list):
                    print(f"[Step3] Skipping directory: {file.filename}")
                    continue
                
                # Get the FULL file content
                full_content = file_content.decoded_content.decode('utf-8')
                cache[file.filename] = full_content
                print(f"[Step3] Cached {file.filename} ({len(full_content)} chars)")
                
            except Exception as e:
                print(f"[Step3] Error caching file {file.filename}: {e}")
                continue
                
    except Exception as e:
        print(f"[Step3] Error initializing dynamic cache: {e}")
        return cache, pr_files
        
    print(f"[Step3] Dynamic cache initialized with {len(cache)} files")
    return cache, pr_files

def fetch_requirements_from_readme(repo_name: str, branch: str) -> str:
    if not github_direct:
        print(f"[DEBUG] Direct GitHub API not available for README")
        return "# No README found\n\nPlease provide coding standards and requirements."
    
    try:
        repo = github_direct.get_repo(repo_name)
        file_content = repo.get_contents("README.md", ref=branch)
        
        if isinstance(file_content, list):
            print(f"README.md is a directory, not a file")
            return "# No README found\n\nPlease provide coding standards and requirements."
        
        content = file_content.decoded_content.decode('utf-8')
        print(f"[DEBUG] Successfully read README.md ({len(content)} chars)")
        return content
        
    except Exception as e:
        print(f"Error reading README.md: {e}")
        return "# No README found\n\nPlease provide coding standards and requirements."

def compose_prompt(requirements: str, code: str, file_name: str, context: str) -> str:
    # Get the file extension for the AI to understand the language
    file_extension = file_name.split('.')[-1].lower()
    
    # Check if this is a package.json file for special dependency handling
    is_package_json = file_name.endswith('package.json')
    
    base_prompt = (
        f"You are an expert AI code reviewer. Your job is to improve the given file `{file_name}` "
        f"by fixing errors and making meaningful improvements while avoiding unnecessary features or new libraries.\n\n"
        f"🎯 **IMPROVEMENT-FOCUSED APPROACH:**\n"
        f"- Fix all syntax errors, missing imports, type issues, and obvious bugs\n"
        f"- Improve code quality, readability, and maintainability\n"
        f"- Optimize performance where beneficial and safe\n"
        f"- Apply modern patterns and best practices when appropriate\n"
        f"- Refactor code for better structure and clarity\n"
        f"- DO NOT add new features or capabilities beyond what's needed\n"
        f"- DO NOT add new libraries unless absolutely necessary for fixes\n"
        f"- Focus on: error fixes, code improvements, performance optimizations, better patterns\n"
        f"- Avoid: adding unnecessary features, introducing new dependencies\n\n"
        f"REQUIREMENTS FROM PROJECT:\n{requirements}\n\n"
        f"---\nRepository Context (other files for reference):\n{context}\n"
        f"---\nCurrent Code ({file_name} - {file_extension} file):\n```{file_extension}\n{code}\n```\n"
    )
    
    if is_package_json:
        dependency_instructions = (
            f"\n---\n🔍 CONSERVATIVE PACKAGE.JSON ANALYSIS:\n"
            f"This is a package.json file. Make ONLY essential dependency corrections:\n\n"
            f"1. **ONLY FIX CRITICAL ISSUES:**\n"
            f"   - Remove dependencies that are clearly unused (not imported anywhere)\n"
            f"   - Add dependencies that are imported but missing\n"
            f"   - Fix obvious version conflicts or syntax errors\n"
            f"   - Ensure dependencies are in the correct section (dependencies vs devDependencies)\n\n"
            f"2. **DO NOT MAKE THESE CHANGES:**\n"
            f"   - Do NOT update versions unless there are compatibility issues\n"
            f"   - Do NOT add new dependencies for 'improvements'\n"
            f"   - Do NOT remove dependencies unless you're 100% sure they're unused\n"
            f"   - Do NOT reorganize or reformat unless there are errors\n\n"
            f"3. **ONLY ANALYZE THE CONTEXT FILES** to see what's actually imported\n"
            f"4. **BE EXTREMELY CONSERVATIVE** - when in doubt, leave it unchanged\n\n"
            f"🚨 CRITICAL: Only make changes that fix actual problems, not 'improvements'\n"
        )
    else:
        dependency_instructions = (
            f"\n---\n📦 CONSERVATIVE DEPENDENCY HANDLING:\n"
            f"For import statements and dependencies:\n"
            f"1. **ONLY FIX ACTUAL ERRORS:**\n"
            f"   - Fix syntax errors in imports\n"
            f"   - Remove unused imports that cause warnings\n"
            f"   - Add missing imports for undefined variables\n"
            f"   - Fix incorrect import paths\n\n"
            f"2. **DO NOT MAKE THESE CHANGES:**\n"
            f"   - Do NOT change import styles unless there are errors\n"
            f"   - Do NOT add new imports for 'improvements'\n"
            f"   - Do NOT restructure imports unless necessary\n"
            f"   - Do NOT update to 'modern' import patterns\n\n"
            f"🚫 PREVENT BUILD ERRORS (ESSENTIAL FIXES ONLY):\n"
            f"1. **Remove unused imports** that will cause build warnings\n"
            f"2. **Add missing imports** for undefined variables/functions\n"
            f"3. **Fix TypeScript errors** (missing types, incorrect syntax)\n"
            f"4. **Correct file paths** if they're wrong\n\n"
            f"📦 DEPENDENCY PHILOSOPHY: If it's not broken, don't fix it\n"
            f"- Be EXTREMELY CONSERVATIVE with any changes\n"
            f"- Only fix actual errors, not 'improvements'\n"
            f"- Do NOT suggest adding dependencies unless absolutely necessary\n"
        )
    
    format_instructions = (
        f"\n---\nPlease return the updated code and changes in the following EXACT format:\n"
        f"### Changes:\n- A bullet-point summary of what was changed (ONLY essential fixes).\n\n"
        f"### Updated Code:\n```{file_extension}\n<ONLY THE NEW/IMPROVED CODE HERE>\n```\n\n"
        f"⚠️ CRITICAL REQUIREMENTS:\n"
        f"1. **CONSERVATIVE APPROACH**: Only make changes that fix actual problems\n"
        f"2. **NO OVER-ENGINEERING**: Do not refactor working code\n"
        f"3. **ESSENTIAL FIXES ONLY**: syntax errors, missing imports, type issues, obvious bugs\n"
        f"4. Do NOT use <think> tags or any other XML-like tags\n"
        f"5. Provide bullet-point summary of changes under the `### Changes` heading\n"
        f"6. Provide ONLY ONE code block under the `### Updated Code` heading.\n"
        f"7. Do NOT show the old code again in your response\n"
        f"8. Do NOT suggest creating new files. Only update this file\n"
        f"9. The response must start with `### Changes:` and end with the code block\n"
        f"10. Return ONLY the corrected code, not refactored code\n"
        f"11. CRITICAL: Return the SAME TYPE of code as the original file ({file_extension})\n"
        f"12. Do NOT convert file types or change file structure\n"
        f"13. **IF NO ESSENTIAL FIXES ARE NEEDED:**\n"
        f"    - In the ### Changes section, write: 'No essential changes needed.'\n"
        f"    - In the ### Updated Code section, return the original code unchanged.\n"
        f"14. **CHANGES MUST BE ESSENTIAL**: Only report actual fixes, not improvements\n"
    )
    
    return base_prompt + dependency_instructions + format_instructions



async def process_single_file(session, file_name: str, old_code: str, requirements: str, pr_info: Optional[dict] = None, dynamic_context_cache: Optional[Dict[str, str]] = None, pr_files: Optional[Set[str]] = None, processed_files: Optional[Set[str]] = None) -> dict:
    """Process a single file through the AI refinement pipeline with dynamic context"""
    try:
        print(f"[Step3] Processing file: {file_name}")
        
        # Fetch context using dynamic cache with dependency optimization, otherwise fall back to static context
        if dynamic_context_cache is not None and pr_files is not None:
            print(f"[Step3] Using DEPENDENCY-OPTIMIZED dynamic context for {file_name}")
            context = fetch_dynamic_context(file_name, dynamic_context_cache, pr_files, processed_files)
        else:
            print(f"[Step3] Falling back to static context for {file_name}")
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
        
        # Extract changes and updated code
        changes = extract_changes(response, file_name)
        updated_code = extract_updated_code(response)
        updated_code = cleanup_extracted_code(updated_code)
        
        # Fallback if no updated code found
        if not updated_code:
            print(f"⚠️ WARNING: Could not extract updated code for {file_name}. Using original code.")
            updated_code = old_code
        
        print(f"[Step3] Successfully processed {file_name}")
        
        # Track external dependencies from the processed file
        external_deps = extract_external_dependencies(file_name, updated_code)
        update_external_dependency_store(file_name, external_deps)
        
        return {
            "old_code": old_code,
            "changes": changes,
            "updated_code": updated_code,
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
    """Main function to regenerate code using MCP with dynamic context updates"""
    regenerated = {}
    server_params = StdioServerParameters(command="python", args=["server.py"])

    # Accumulate total token usage
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    # Initialize dynamic context cache and external dependency store
    if pr_info:
        repo_name = pr_info["repo_name"]
        pr_number = pr_info["pr_number"]
        dynamic_context_cache, pr_files = initialize_dynamic_context_cache(repo_name, pr_number, pr_info)
        print(f"[Step3] 🎯 Dynamic context cache initialized with {len(dynamic_context_cache)} files")
    else:
        dynamic_context_cache = {}
        pr_files = set(files.keys())
        # Initialize cache with current file contents
        for file_name, file_content in files.items():
            dynamic_context_cache[file_name] = file_content
    
    # Initialize external dependency store for package.json optimization
    global EXTERNAL_DEPENDENCY_STORE
    EXTERNAL_DEPENDENCY_STORE = {}
    print(f"[Step3] 📦 External dependency store initialized for package.json optimization")

    # Track which files have been processed for context building
    processed_files = set()
    total_files = len(files)
    current_file_number = 0

    # Separate package.json files to process them last (for dependency analysis)
    package_json_files = {}
    regular_files = {}
    
    for file_name, file_content in files.items():
        if file_name.endswith('package.json'):
            package_json_files[file_name] = file_content
        else:
            regular_files[file_name] = file_content
    
    # Create ordered processing list: regular files first, then package.json files
    ordered_files = list(regular_files.items()) + list(package_json_files.items())
    
    print(f"[Step3] 📦 Processing order optimized for dependencies and context:")
    print(f"[Step3]   - Regular files first: {len(regular_files)} files (dependency-based context)")
    print(f"[Step3]   - Package.json files last: {len(package_json_files)} files (full context)")
    print(f"[Step3]   - This ensures package.json sees all refined dependencies for comprehensive analysis")

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                # Process files in dependency-optimized order
                for file_name, old_code in ordered_files:
                    current_file_number += 1
                    
                    # Special indicator for package.json files
                    if file_name.endswith('package.json'):
                        print(f"[Step3] 📦 Processing PACKAGE.JSON {current_file_number}/{total_files}: {file_name}")
                        print(f"[Step3] 🔍 FULL CONTEXT MODE: AI will analyze all refined files for comprehensive dependency analysis")
                    else:
                        print(f"[Step3] 🎯 Processing file {current_file_number}/{total_files}: {file_name}")
                        print(f"[Step3] 🔗 DEPENDENCY-BASED CONTEXT: Only including relevant imported files")
                    
                    print(f"[Step3] 📊 Context status: {len(processed_files)} files already refined, {total_files - current_file_number} files remaining")
                    
                    print(f"[Step3] 🔧 Using regular MCP for code generation: {file_name}")
                    file_result = await process_single_file(
                        session, 
                        file_name, 
                        old_code, 
                        requirements, 
                        pr_info,
                        dynamic_context_cache,
                        pr_files,
                        processed_files
                    )
                    
                    # Store the result
                    regenerated[file_name] = file_result
                    
                    # Update dynamic cache with refined version for future files
                    updated_code = file_result.get("updated_code", old_code)
                    if updated_code != old_code:
                        dynamic_context_cache[file_name] = updated_code
                        processed_files.add(file_name)
                        print(f"[Step3] ✅ Updated dynamic cache for {file_name} ({len(updated_code)} chars) - REFINED")
                    else:
                        print(f"[Step3] 📄 No changes for {file_name} - keeping original in cache")
                        processed_files.add(file_name)  # Still mark as processed even if no changes
        print(f"[Step3] 📊 Processing Summary:")
        print(f"[Step3]   - Total files processed: {len(regenerated)}")
        print(f"[Step3]   - Regular files refined: {len(regular_files)}")
        print(f"[Step3]   - Package.json files analyzed: {len(package_json_files)}")
        print(f"[Step3]   - External dependencies tracked: {len(EXTERNAL_DEPENDENCY_STORE)} files contributed to dependency analysis")

        
        # Print final token usage and pricing
        print(f"[Step3] 💰 TOTAL TOKEN USAGE: prompt_tokens={total_prompt_tokens:,}, completion_tokens={total_completion_tokens:,}, total_tokens={total_tokens:,}")

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

async def fix_package_json_with_llm(package_json_content, npm_error, package_file_path, pr_info):
    """
    Use LLM to fix package.json based on npm install errors.
    Returns corrected package.json content or None if correction fails.
    """
    if not pr_info:
        print("[LocalRepo] 🔧 No PR info available for LLM error correction")
        return None
    
    print(f"[LocalRepo] 🤖 Using LLM to fix package.json errors...")
    
    # Create error correction prompt
    error_correction_prompt = f"""You are an expert package.json dependency resolver. An npm install failed with the following error:

---
NPM INSTALL ERROR:
{npm_error}
---

Current package.json content:
```json
{package_json_content}
```

You are fixing package.json dependency issues. The most common npm install errors and their solutions:

1. **"No matching version found for package@version"** - The specified version doesn't exist
   - Solution: Use a version that actually exists (e.g., ^15.0.0 instead of ^16.3.0)
   - Check semver ranges carefully - use tilde (~) for patch updates, caret (^) for minor updates

2. **"Package not found"** - Package name is wrong or deprecated
   - Solution: Fix the package name or find an alternative

3. **"Peer dependency warnings/conflicts"**
   - Solution: Align versions between packages that depend on each other

4. **"ETARGET" errors** - Version targeting issues
   - Solution: Use exact versions or valid ranges that exist in npm registry

🔍 **CRITICAL INSTRUCTIONS:**
1. READ the npm error message VERY CAREFULLY to identify the exact problematic package and version
2. For "No matching version found" errors: **ALWAYS LOWER the version number - NEVER GO UP!**
   - ⚠️ **LOGIC**: If version 9.34 doesn't exist, then 9.35 (released later) also won't exist!
   - ✅ **CORRECT**: If husky@^9.6.2 fails → try husky@^9.0.0 or husky@^8.0.0 (going DOWN)
   - ✅ **CORRECT**: If lint-staged@^16.3.0 fails → try lint-staged@^15.0.0 or lint-staged@^14.0.0 (going DOWN)
   - ❌ **NEVER DO**: If eslint@^9.34.0 fails → DON'T try eslint@^9.35.0 (that would be going UP!)
   - 🎯 **STRATEGY**: Drop major/minor version significantly when a version doesn't exist
3. PRESERVE all functionality - don't remove packages unless they're truly unnecessary
4. Use CONSERVATIVE version ranges - better to be too low than too high
5. Keep JSON structure clean and valid
6. ONLY change the packages/versions that are causing the specific error

⚠️ **VERSION CORRECTION RULES:**
- If a version doesn't exist, ALWAYS try a LOWER version that is known to be stable
- Use stable major versions (e.g., ^8.0.0, ^7.0.0) rather than bleeding edge
- When in doubt, go back to LTS or well-established versions

Return your response in this EXACT format:

### Analysis:
- Brief explanation of what was wrong and what you fixed

### Fixed package.json:
```json
<CORRECTED PACKAGE.JSON CONTENT HERE>
```

IMPORTANT: Return ONLY the corrected package.json in the code block, not the original."""

    try:
        # Use the existing MCP infrastructure
        server_params = StdioServerParameters(command="python", args=["server.py"])
        
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                
                # Call LLM for error correction
                result = await asyncio.wait_for(
                    session.call_tool("codegen", arguments={"prompt": error_correction_prompt}),
                    timeout=120  # 2 minute timeout for error correction
                )
                
                # Extract response
                response = extract_response_content(result, package_file_path)
                
                # Parse the corrected package.json
                corrected_json = extract_updated_code(response)
                if corrected_json:
                    # Validate it's valid JSON
                    try:
                        json.loads(corrected_json)
                        print(f"[LocalRepo] ✅ LLM provided valid package.json correction")
                        return corrected_json
                    except json.JSONDecodeError as e:
                        print(f"[LocalRepo] ❌ LLM correction produced invalid JSON: {e}")
                        return None
                else:
                    print(f"[LocalRepo] ❌ Could not extract corrected package.json from LLM response")
                    return None
                    
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during LLM package.json correction: {e}")
        return None

async def fix_package_json_for_build_errors(package_json_content, build_error, package_dir_path, pr_info):
    """
    Use LLM to fix package.json based on build errors that indicate missing dependencies.
    Returns corrected package.json content or None if correction fails.
    """
    if not pr_info:
        print("[LocalRepo] 🔧 No PR info available for LLM package.json build error correction")
        return None
    
    print(f"[LocalRepo] 🤖 Using LLM to fix package.json for build dependency errors...")
    
    # Create package.json correction prompt for build errors
    build_error_package_prompt = f"""You are an expert package.json dependency resolver. A build failed with errors indicating missing dependencies:

---
BUILD ERROR:
{build_error}
---

Current package.json content:
```json
{package_json_content}
```

🔍 **BUILD ERROR ANALYSIS:**
The build errors indicate missing dependencies. Common patterns:
1. **"Cannot find module 'package-name'"** - The package is missing from dependencies
2. **"Cannot find module 'package-name' or its corresponding type declarations"** - Missing package AND its @types/* package
3. **"Cannot find type definition file for 'node'"** - Missing @types/node in devDependencies

📦 **DEPENDENCY PHILOSOPHY: More dependencies = More problems**
- Only add dependencies that are ACTUALLY NEEDED based on the build errors
- Do NOT add speculative dependencies
- Be CONSERVATIVE - only fix what's broken

🔧 **CRITICAL INSTRUCTIONS:**
1. ANALYZE the build error to identify exactly which packages are missing
2. ADD missing packages to the appropriate section:
   - Runtime dependencies → "dependencies" 
   - Type definitions → "devDependencies" (all @types/* packages)
   - Development tools → "devDependencies"
3. For missing type declarations, add both the package AND its @types/* if needed
4. Use CONSERVATIVE versions - stable, well-tested versions
5. Do NOT remove existing dependencies unless they're clearly wrong
6. Keep JSON structure clean and valid

**Examples of fixes:**
- Error: "Cannot find module 'react-router-dom'" → Add "react-router-dom": "^6.8.0" to dependencies
- Error: "Cannot find module 'react-router-dom' or its corresponding type declarations" → Add both the package AND "@types/react-router-dom" if needed
- Error: "Cannot find type definition file for 'node'" → Add "@types/node": "^18.0.0" to devDependencies

Return your response in this EXACT format:

### Analysis:
- Brief explanation of what dependencies were missing and what you added

### Fixed package.json:
```json
<CORRECTED PACKAGE.JSON CONTENT HERE>
```

IMPORTANT: Return ONLY the corrected package.json in the code block, not the original."""

    try:
        # Use the existing MCP infrastructure
        server_params = StdioServerParameters(command="python", args=["server.py"])
        
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                
                # Call LLM for package.json correction based on build errors
                result = await asyncio.wait_for(
                    session.call_tool("codegen", arguments={"prompt": build_error_package_prompt}),
                    timeout=120  # 2 minute timeout for package.json correction
                )
                
                # Extract response
                response = extract_response_content(result, "package_json_build_error_correction")
                
                # Parse the corrected package.json
                corrected_json = extract_updated_code(response)
                if corrected_json:
                    # Validate it's valid JSON
                    try:
                        json.loads(corrected_json)
                        print(f"[LocalRepo] ✅ LLM provided valid package.json correction for build errors")
                        return corrected_json
                    except json.JSONDecodeError as e:
                        print(f"[LocalRepo] ❌ LLM correction produced invalid JSON: {e}")
                        return None
                else:
                    print(f"[LocalRepo] ❌ Could not extract corrected package.json from LLM response")
                    return None
                    
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during LLM package.json build error correction: {e}")
        return None


async def fix_build_errors_with_llm(build_error, affected_files, package_dir_path, pr_info):
    """
    Use LLM to fix source code files based on npm build errors.
    Returns dict of corrected files or None if correction fails.
    """
    if not pr_info:
        print("[LocalRepo] 🔧 No PR info available for LLM build error correction")
        return None
    
    print(f"[LocalRepo] 🤖 Using LLM to fix build errors...")
    
    # Analyze the build error to identify problematic files
    affected_file_contents = {}
    for file_path in affected_files:
        full_path = os.path.join(package_dir_path, file_path)
        if os.path.exists(full_path):
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    affected_file_contents[file_path] = f.read()
            except Exception as e:
                print(f"[LocalRepo] ⚠️ Could not read {file_path}: {e}")
                continue
    
    if not affected_file_contents:
        print("[LocalRepo] ❌ No affected files could be read for build error correction")
        return None
    
    # Create build error correction prompt
    files_context = ""
    for file_path, content in affected_file_contents.items():
        files_context += f"\n\n--- {file_path} ---\n```\n{content}\n```"
    
    build_error_correction_prompt = f"""You are an expert TypeScript/JavaScript developer. A build failed with the following error:

---
BUILD ERROR:
{build_error}
---

Affected files that need to be fixed:
{files_context}

 🚫 **COMMON BUILD ERRORS AND SOLUTIONS:**

 1. **Unused Import Errors** (`'X' is declared but its value is never read`)
    - Solution: Remove the unused import from the import statement
    - Example: If `useEffect` is imported but not used, remove it from the React import

 2. **Type-only Import Errors** (`'X' is a type and must be imported using a type-only import when 'verbatimModuleSyntax' is enabled`)
    - Solution: Change regular imports to type-only imports for types
    - Example: Change 'import {{ FormEvent }}' to 'import type {{ FormEvent }}'
    - Only applies to TypeScript types, not runtime values

 3. **Cannot find module errors** (`Cannot find module 'package-name'`)
    - Solution: The dependency is missing from package.json or the import path is wrong
    - Check if the module should be available and fix the import path

 4. **TypeScript Configuration Errors** (tsconfig.json issues)
    - **Unknown compiler option**: Remove unsupported options like `noUncheckedSideEffectImports`
    - **Invalid target values**: Use valid ES targets like 'es2020', 'es2021', 'es2022', 'esnext'
    - **Missing required options**: Add `"incremental": true` when using `tsBuildInfoFile`
    - **Type definition errors**: Fix `types` array or add missing @types/* packages

 5. **TypeScript type definition errors** (`Cannot find type definition file for 'node'`)
    - Solution: Remove 'node' from types array if @types/node is not installed
    - Or ensure @types/node is properly listed in package.json devDependencies

 6. **File path errors** (incorrect relative/absolute paths)
    - Solution: Fix the import paths to point to existing files
    - Use correct case sensitivity

 7. **JSX Extension Errors** (Parse error with JSX in .js files)
    - Solution: Rename .js files containing JSX to .jsx extension
    - Example: Rename `App.js` to `App.jsx` if it contains JSX syntax
    - This is a common Vite/React issue where .js files with JSX need .jsx extension

📦 **DEPENDENCY PHILOSOPHY: More dependencies = More problems**
- Be EXTREMELY CONSERVATIVE about suggesting new dependencies
- Try to fix errors by removing unused code rather than adding dependencies
- Only suggest adding a dependency if it's absolutely essential
- Prefer using existing, already-installed dependencies

🔍 **CRITICAL INSTRUCTIONS:**
1. ANALYZE the build error message carefully to identify the exact problem
2. FIX only what's broken - don't make unnecessary changes
3. REMOVE unused imports, variables, and functions causing warnings
4. ENSURE all imports have corresponding available dependencies
5. VERIFY file paths are correct and files exist
6. Do NOT add new dependencies unless absolutely necessary
7. Keep the functionality intact while fixing the build errors

Return your response in this EXACT format:

### Analysis:
- Brief explanation of the build errors and how you fixed them

### Fixed Files:
For each file that needs changes, use this format:

 #### {file_path}
 ```
 <CORRECTED FILE CONTENT HERE>
 ```

**IMPORTANT FILE RENAMING INSTRUCTIONS:**
- If you suggest renaming a file (e.g., .js to .jsx), provide the new filename in the header
- Example: `#### src/App.jsx` (instead of `#### src/App.js`)
- The system will automatically handle the file rename operation
- Make sure to mention the rename in your Analysis section

IMPORTANT: 
- Only include files that actually need changes
- Return the complete corrected file content for each file
- Do NOT suggest adding new dependencies unless absolutely essential
- Focus on removing unused code rather than adding new code
- If suggesting a file rename, use the new filename in the header"""

    try:
        # Use the existing MCP infrastructure
        server_params = StdioServerParameters(command="python", args=["server.py"])
        
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                
                # Call LLM for build error correction
                result = await asyncio.wait_for(
                    session.call_tool("codegen", arguments={"prompt": build_error_correction_prompt}),
                    timeout=180  # 3 minute timeout for build error correction
                )
                
                # Extract response
                response = extract_response_content(result, "build_error_correction")
                
                # Parse the corrected files from the response
                corrected_files = {}
                
                # Enhanced patterns to extract file corrections from various LLM response formats
                patterns = [
                    # Pattern 1: #### filename with code block (original format)
                    r'#### ([^\n{]+)\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
                    # Pattern 2: Look for actual filenames (not placeholders) in the affected files
                    r'#### (src/[^\n]+\.tsx?)\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
                    r'#### (src/[^\n]+\.jsx?)\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
                    r'#### ([^\n]+\.tsx?)\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
                    r'#### ([^\n]+\.jsx?)\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
                    # Pattern 3: ### Fixed Files: with actual file names
                    r'### Fixed Files:[\s\S]*?#### (src/[^\n]+\.tsx?)\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
                    # Pattern 4: Direct code blocks after file mentions
                    r'(src/[A-Za-z0-9_./\-]+\.tsx?)[\s\S]*?```[a-zA-Z0-9]*\n([\s\S]*?)```',
                    # Pattern 5: Any TypeScript/JavaScript file followed by code
                    r'([A-Za-z0-9_./\-]+\.(?:tsx?|jsx?))\s*:?\s*```[a-zA-Z0-9]*\n([\s\S]*?)```'
                ]
                
                for i, pattern in enumerate(patterns):
                    file_sections = re.findall(pattern, response, re.IGNORECASE)
                    
                    for file_path, file_content in file_sections:
                        file_path = file_path.strip()
                        file_content = file_content.strip()
                        
                        # Check if this file is one we're trying to fix
                        if file_path in affected_file_contents:
                            corrected_files[file_path] = file_content
                            print(f"[LocalRepo] ✅ LLM provided correction for {file_path} (pattern {i+1})")
                        elif any(file_path.endswith(af) for af in affected_files):
                            # Handle case where path might be slightly different
                            corrected_files[file_path] = file_content
                            print(f"[LocalRepo] ✅ LLM provided correction for {file_path} (pattern {i+1}, matched by suffix)")
                    
                    if corrected_files:
                        break  # Found files with this pattern, stop trying others
                
                if corrected_files:
                    return corrected_files
                else:
                    # Enhanced debugging: try to understand what the LLM actually provided
                    print(f"[LocalRepo] ❌ Could not extract corrected files from LLM response")
                    print(f"[LocalRepo] 🔍 Looking for mentions of affected files in response...")
                    
                    for affected_file in affected_files:
                        if affected_file in response:
                            print(f"[LocalRepo] ✓ Found mention of {affected_file} in response")
                        else:
                            print(f"[LocalRepo] ✗ No mention of {affected_file} in response")
                    
                    # Look for any code blocks at all
                    code_blocks = re.findall(r'```[a-zA-Z0-9]*\n([\s\S]*?)```', response)
                    print(f"[LocalRepo] 🔍 Found {len(code_blocks)} code blocks in response")
                    
                    if code_blocks:
                        print(f"[LocalRepo] 🔍 First code block preview: {code_blocks[0][:200]}...")
                        
                        # If there's exactly one code block and one affected file, try to match them
                        if len(code_blocks) == 1 and len(affected_files) == 1:
                            print(f"[LocalRepo] 🎯 Attempting to match single code block to single affected file")
                            corrected_files[affected_files[0]] = code_blocks[0].strip()
                            return corrected_files
                    
                    print(f"[LocalRepo] 🔍 Full response preview: {response[:1000]}...")
                    
                    # Special handling for JSX extension issues
                    if 'jsx' in response.lower() or 'extension' in response.lower():
                        print(f"[LocalRepo] 🔍 Detected JSX/extension suggestions in LLM response")
                        for affected_file in affected_files:
                            if affected_file.endswith('.js') and affected_file in affected_file_contents:
                                new_file = affected_file.replace('.js', '.jsx')
                                current_content = affected_file_contents[affected_file]
                                converted_content = convert_commonjs_to_es_modules(current_content)
                                corrected_files[new_file] = converted_content
                                corrected_files[affected_file] = None  # Mark for removal
                                print(f"[LocalRepo] ✅ LLM suggested rename: {affected_file} → {new_file}")
                                return corrected_files
                    
                    return None
                    
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during LLM build error correction: {e}")
        return None

def run_npm_install_with_error_correction(package_dir_path, package_file, repo_path, regenerated_files, pr_info):
    """
    Run npm install with intelligent error correction using LLM in a loop.
    Keeps trying until success or max retries reached.
    Returns True if successful, False otherwise.
    """
    package_dir = os.path.dirname(package_file) if os.path.dirname(package_file) else "."
    MAX_CORRECTION_ATTEMPTS = 10  # Maximum number of LLM correction attempts
    
    def attempt_npm_install():
        """Helper function to attempt npm install"""
        try:
            result = subprocess.run(
                ["npm", "install", "--legacy-peer-deps"],
                cwd=package_dir_path,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            return result
        except subprocess.TimeoutExpired:
            print(f"[LocalRepo] ❌ npm install timed out after 5 minutes in {package_dir}")
            return None
        except FileNotFoundError:
            print("[LocalRepo] ❌ npm not found. Please ensure Node.js and npm are installed.")
            return None
        except Exception as e:
            print(f"[LocalRepo] ❌ Error during npm install in {package_dir}: {e}")
            return None

    def generate_lockfile_if_exists():
        """Helper function to generate lockfile after successful npm install"""
        if package_dir == ".":
            lockfile_relative_path = "package-lock.json"
        else:
            lockfile_relative_path = os.path.join(package_dir, "package-lock.json")
        
        lockfile_absolute_path = os.path.join(repo_path, lockfile_relative_path)
        
        if os.path.exists(lockfile_absolute_path):
            with open(lockfile_absolute_path, "r", encoding="utf-8") as f:
                lockfile_content = f.read()
            
            # Add the lockfile to regenerated_files for GitHub API push
            regenerated_files[lockfile_relative_path] = {
                "old_code": "",
                "changes": f"Regenerated lockfile after {package_file} update via npm install",
                "updated_code": lockfile_content
            }
            print(f"[LocalRepo] ✅ Generated and added {lockfile_relative_path} to commit")
        else:
            print(f"[LocalRepo] ⚠️ Warning: package-lock.json not generated by npm install in {package_dir}")

    # Initial attempt
    print(f"[LocalRepo] 📦 Attempting npm install in {package_dir_path}")
    result = attempt_npm_install()
    
    if result is None:
        return False
    
    if result.returncode == 0:
        print(f"[LocalRepo] ✅ npm install completed successfully in {package_dir}")
        generate_lockfile_if_exists()
        return True
    
    # Start the correction loop
    print(f"[LocalRepo] 🔄 Starting LLM error correction loop (max {MAX_CORRECTION_ATTEMPTS} attempts)...")
    
    original_package_json = None
    correction_history = []  # Track all corrections applied
    
    for attempt in range(1, MAX_CORRECTION_ATTEMPTS + 1):
        print(f"[LocalRepo] 🤖 LLM Correction Attempt {attempt}/{MAX_CORRECTION_ATTEMPTS}")
        
        # Check if result is valid before accessing attributes
        if result is None:
            print(f"[LocalRepo] ❌ npm install attempt returned None (timeout/error)")
            break
            
        print(f"[LocalRepo] ❌ npm install failed with return code {result.returncode}")
        print(f"[LocalRepo] 📄 Error output:")
        print(f"[LocalRepo] stdout: {result.stdout}")
        print(f"[LocalRepo] stderr: {result.stderr}")
        
        # Get current package.json content
        package_json_path = os.path.join(package_dir_path, "package.json")
        try:
            with open(package_json_path, "r", encoding="utf-8") as f:
                current_package_json = f.read()
                
            # Store original on first correction attempt
            if original_package_json is None:
                original_package_json = current_package_json
                
        except Exception as e:
            print(f"[LocalRepo] ❌ Could not read package.json for error correction: {e}")
            return False
        
        # Combine error output for LLM analysis
        full_error = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        
        # Enhanced prompt with attempt context
        correction_context = ""
        if correction_history:
            correction_context = f"\n\nPREVIOUS CORRECTIONS APPLIED:\n" + "\n".join([f"Attempt {i+1}: {corr}" for i, corr in enumerate(correction_history)])
        
        print(f"[LocalRepo] 🤖 Sending error to LLM for analysis...")
        
        try:
            corrected_package_json = asyncio.run(
                fix_package_json_for_build_errors(
                    current_package_json, 
                    full_error + correction_context, 
                    package_file, 
                    pr_info
                )
            )
        except Exception as e:
            print(f"[LocalRepo] ❌ Error running LLM correction: {e}")
            continue  # Try next iteration
        
        if not corrected_package_json:
            print(f"[LocalRepo] ❌ LLM could not provide a valid correction for attempt {attempt}")
            continue  # Try next iteration
        
        # Check if LLM made any actual changes
        if corrected_package_json.strip() == current_package_json.strip():
            print(f"[LocalRepo] ⚠️ LLM returned same package.json (no changes) - may be stuck")
            continue  # Try next iteration
        
        # Write corrected package.json back to file
        try:
            with open(package_json_path, "w", encoding="utf-8") as f:
                f.write(corrected_package_json)
            print(f"[LocalRepo] ✅ Applied LLM correction attempt {attempt}")
            
            # Track this correction
            correction_history.append(f"Fixed dependency issues in npm install")
            
        except Exception as e:
            print(f"[LocalRepo] ❌ Error writing corrected package.json: {e}")
            continue  # Try next iteration
        
        # Retry npm install with corrected package.json
        print(f"[LocalRepo] 🔄 Retrying npm install with LLM correction {attempt}...")
        result = attempt_npm_install()
        
        if result is None:
            continue  # Try next iteration
        
        if result.returncode == 0:
            print(f"[LocalRepo] 🎉 npm install succeeded after {attempt} LLM correction(s)!")
            
            # Update regenerated_files with the final corrected version
            correction_summary = f"LLM-corrected package.json after {attempt} iteration(s) to fix npm install failures"
            regenerated_files[package_file] = {
                "old_code": original_package_json or current_package_json,
                "changes": correction_summary,
                "updated_code": corrected_package_json
            }
            
            generate_lockfile_if_exists()
            return True
        
        # If we get here, this correction didn't work, continue to next attempt
        print(f"[LocalRepo] ⚠️ npm install still failed after correction {attempt}, trying next iteration...")
    
    # All correction attempts exhausted
    print(f"[LocalRepo] ❌ npm install failed after {MAX_CORRECTION_ATTEMPTS} LLM correction attempts")
    if result is not None:
        print(f"[LocalRepo] 📄 Final error output:")
        print(f"[LocalRepo] stdout: {result.stdout}")
        print(f"[LocalRepo] stderr: {result.stderr}")
    else:
        print(f"[LocalRepo] 📄 Final attempt resulted in timeout/error (no output available)")
    
    # Store the final attempted correction even if it failed
    if original_package_json and correction_history:
        with open(os.path.join(package_dir_path, "package.json"), "r", encoding="utf-8") as f:
            final_package_json = f.read()
        
        correction_summary = f"LLM attempted {len(correction_history)} correction(s) but npm install still failed"
        regenerated_files[package_file] = {
            "old_code": original_package_json,
            "changes": correction_summary,
            "updated_code": final_package_json
        }
    
    return False

def run_npm_build_with_error_correction(repo_path, package_json_files, regenerated_files, pr_info):
    """
    Run npm build with intelligent error correction using LLM in a loop.
    Keeps trying until success or max retries reached for each package.
    Returns build status string.
    """
    if not package_json_files:
        print("[LocalRepo] 🏗️ No package.json files found, skipping npm build validation")
        return "SKIPPED - No package.json files"
    
    print(f"[LocalRepo] 🏗️ Starting npm build validation with intelligent error correction...")
    
    build_results = []
    MAX_BUILD_CORRECTION_ATTEMPTS = 15  # Maximum number of LLM correction attempts per package
    
    def attempt_npm_build(package_dir_path):
        """Helper function to attempt npm build"""
        try:
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=package_dir_path,
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout for build
            )
            return result
        except subprocess.TimeoutExpired:
            print(f"[LocalRepo] ❌ npm run build timed out after 10 minutes")
            return None
        except FileNotFoundError:
            print("[LocalRepo] ❌ npm not found for build. Please ensure Node.js and npm are installed.")
            return None
        except Exception as e:
            print(f"[LocalRepo] ❌ Error during npm run build: {e}")
            return None

    async def extract_affected_files_from_error_with_llm(build_error, package_dir_path, pr_info):
        """
        Use LLM to intelligently extract affected files from build errors.
        
        This replaces regex-based file detection which couldn't handle:
        - Vite timestamp files (vite.config.ts.timestamp-xxxxx.mjs)
        - Complex webpack transformations
        - Unusual build tool error formats
        
        The LLM can understand the semantic meaning of errors and identify
        the actual source files that need to be fixed.
        """
        print(f"[LocalRepo] 🧠 Using INTELLIGENT LLM file identification (handles Vite timestamps, webpack transforms, etc.)")
        
        # Get list of all files in the package directory for reference
        all_files = []
        try:
            for root, dirs, files in os.walk(package_dir_path):
                # Skip directories that should never be modified
                dirs[:] = [d for d in dirs if d not in ['node_modules', '.git', 'dist', 'build', '.next', '.nuxt', 'coverage', '.nyc_output']]
                
                for file in files:
                    if file.endswith(('.ts', '.tsx', '.js', '.jsx', '.vue', '.json', '.css', '.scss', '.html')):
                        rel_path = os.path.relpath(os.path.join(root, file), package_dir_path)
                        # Double-check: never include node_modules files
                        if not rel_path.startswith('node_modules/') and not '/node_modules/' in rel_path:
                            all_files.append(rel_path)
        except Exception as e:
            print(f"[LocalRepo] ⚠️ Error scanning directory: {e}")
        
        file_identification_prompt = f"""You are analyzing a build error to identify which specific files need to be fixed. 

BUILD ERROR:
{build_error}

Available files in the project:
{chr(10).join(all_files[:50])}  {"... (truncated)" if len(all_files) > 50 else ""}

TASK: Identify the specific files that are causing this build error and need to be modified to fix it.

IMPORTANT GUIDELINES:
1. Look for file paths mentioned in the error (even with timestamps or temporary extensions)
2. Consider the root cause - if a dependency is missing, the importing file needs the dependency added
3. If there are TypeScript config errors, identify the config files that need changes
4. Be specific - return only files that actually exist and need modification
5. Strip any temporary extensions/timestamps to get the real file names
6. NEVER suggest files from node_modules/ - these are external dependencies that should not be modified
7. NEVER suggest files from .git/, dist/, build/, .next/, .nuxt/, coverage/ - these are generated files
8. Only suggest source files that are part of the project codebase

Return ONLY a JSON array of file paths, relative to the project root:
["file1.ts", "src/file2.tsx", "tsconfig.json"]

Do not include any explanation, just the JSON array."""

        try:
            # Use MCP to identify files
            server_params = StdioServerParameters(command="python", args=["server.py"])
            
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    
                    # Call LLM for file identification
                    result = await asyncio.wait_for(
                        session.call_tool("codegen", arguments={"prompt": file_identification_prompt}),
                        timeout=120  # 2 minute timeout for file identification
                    )
                    
                    # Extract response content
                    response_text = extract_response_content(result, "file_identification")
                    if response_text:
                        response_text = response_text.strip()
                    else:
                        response_text = ""
                    print(f"[LocalRepo] 🤖 LLM response: {response_text}")
                    
                    # Parse the JSON response - handle both markdown code blocks and raw JSON
                    try:
                        # First try to extract JSON from markdown code blocks
                        json_content = None
                        
                        # Pattern 1: ```json ... ```
                        json_block_match = re.search(r'```json\s*\n?(.*?)```', response_text, re.DOTALL)
                        if json_block_match:
                            json_content = json_block_match.group(1).strip()
                            print(f"[LocalRepo] 🔍 Extracted JSON from markdown code block")
                        
                        # Pattern 2: ``` ... ``` (generic code block)
                        elif '```' in response_text:
                            code_block_match = re.search(r'```\s*\n?(.*?)```', response_text, re.DOTALL)
                            if code_block_match:
                                potential_json = code_block_match.group(1).strip()
                                # Check if it looks like a JSON array
                                if potential_json.startswith('[') and potential_json.endswith(']'):
                                    json_content = potential_json
                                    print(f"[LocalRepo] 🔍 Extracted JSON from generic code block")
                        
                        # Pattern 3: Raw JSON (fallback)
                        if not json_content:
                            json_content = response_text.strip()
                            print(f"[LocalRepo] 🔍 Using raw response as JSON")
                        
                        # Parse the extracted JSON
                        affected_files = json.loads(json_content)
                        
                        if isinstance(affected_files, list):
                            # Filter to only files that actually exist and are not in forbidden directories
                            existing_files = []
                            for file_path in affected_files:
                                # Never allow node_modules or other forbidden directories
                                if (file_path.startswith('node_modules/') or 
                                    '/node_modules/' in file_path or
                                    file_path.startswith('.git/') or
                                    file_path.startswith('dist/') or
                                    file_path.startswith('build/') or
                                    file_path.startswith('.next/') or
                                    file_path.startswith('.nuxt/') or
                                    file_path.startswith('coverage/')):
                                    print(f"[LocalRepo] 🚫 LLM suggested forbidden file {file_path} - ignoring")
                                    continue
                                    
                                full_path = os.path.join(package_dir_path, file_path)
                                if os.path.exists(full_path):
                                    existing_files.append(file_path)
                                else:
                                    print(f"[LocalRepo] ⚠️ LLM suggested {file_path} but file doesn't exist")
                            
                            print(f"[LocalRepo] ✅ LLM identified {len(existing_files)} affected files: {existing_files}")
                            return existing_files
                        else:
                            print(f"[LocalRepo] ❌ LLM response is not a valid array: {type(affected_files)}")
                            return None
                            
                    except json.JSONDecodeError as e:
                        print(f"[LocalRepo] ❌ Could not parse LLM response as JSON: {e}")
                        print(f"[LocalRepo] 🔍 Raw response: {response_text[:200]}...")
                        return None
                        
        except Exception as e:
            print(f"[LocalRepo] ❌ Error with MCP file identification: {e}")
            return None
    
    for package_file in package_json_files.keys():
        # Get the directory containing the package.json
        package_dir = os.path.dirname(package_file) if os.path.dirname(package_file) else "."
        package_dir_path = os.path.join(repo_path, package_dir)
        
        # Check if build script exists in package.json
        package_json_path = os.path.join(package_dir_path, "package.json")
        try:
            with open(package_json_path, "r", encoding="utf-8") as f:
                package_data = json.loads(f.read())
                scripts = package_data.get("scripts", {})
                
                if "build" not in scripts:
                    print(f"[LocalRepo] 🏗️ No 'build' script found in {package_file}, skipping")
                    build_results.append(f"{package_file}: NO BUILD SCRIPT")
                    continue
        except Exception as e:
            print(f"[LocalRepo] ❌ Error reading {package_file}: {e}")
            build_results.append(f"{package_file}: READ ERROR")
            continue
        
        print(f"[LocalRepo] 🏗️ Running 'npm run build' in directory: {package_dir_path}")
        
        # Initial build attempt
        result = attempt_npm_build(package_dir_path)
        
        if result is None:
            build_results.append(f"{package_file}: BUILD ERROR")
            continue
        
        if result.returncode == 0:
            print(f"[LocalRepo] ✅ npm run build completed successfully in {package_dir}")
            build_results.append(f"{package_file}: SUCCESS")
            
            # Check if build generated artifacts (optional - just for logging)
            build_dir = os.path.join(package_dir_path, "build")
            dist_dir = os.path.join(package_dir_path, "dist")
            if os.path.exists(build_dir):
                print(f"[LocalRepo] 📁 Build artifacts generated in {package_dir}/build/")
            elif os.path.exists(dist_dir):
                print(f"[LocalRepo] 📁 Build artifacts generated in {package_dir}/dist/")
            continue
        
        # Build failed - start correction loop
        print(f"[LocalRepo] 🔄 Starting LLM build error correction loop (max {MAX_BUILD_CORRECTION_ATTEMPTS} attempts)...")
        
        correction_history = []  # Track all corrections applied
        build_success = False
        
        for attempt in range(1, MAX_BUILD_CORRECTION_ATTEMPTS + 1):
            print(f"[LocalRepo] 🤖 LLM Build Correction Attempt {attempt}/{MAX_BUILD_CORRECTION_ATTEMPTS}")
            
            if result is None:
                print(f"[LocalRepo] ❌ npm run build failed (timeout/error)")
                break
                
            print(f"[LocalRepo] ❌ npm run build failed with return code {result.returncode}")
            print(f"[LocalRepo] 📄 Build error output:")
            print(f"[LocalRepo] stdout: {result.stdout}")
            print(f"[LocalRepo] stderr: {result.stderr}")
            
            # Combine error output for analysis
            full_build_error = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
            
            # Check if this is a dependency-related build error
            is_dependency_error = any(keyword in full_build_error.lower() for keyword in [
                'cannot find module', 'module not found', 'missing dependency', 
                'package not found', 'type declarations', 'cannot resolve'
            ])
            
            if is_dependency_error:
                print(f"[LocalRepo] 🔍 Detected dependency-related build error. Checking package.json...")
                
                # Try to fix package.json first for dependency errors
                package_json_path = os.path.join(package_dir_path, "package.json") 
                if os.path.exists(package_json_path):
                    try:
                        with open(package_json_path, "r", encoding="utf-8") as f:
                            current_package_json = f.read()
                        
                        print(f"[LocalRepo] 🤖 Using LLM to fix package.json for dependency errors...")
                        
                        # Always use web search to fix package.json based on build dependency errors
                        corrected_package_json = asyncio.run(
                            fix_package_json_for_build_errors(
                                current_package_json,
                                full_build_error,
                                package_dir_path,
                                pr_info
                            )
                        )
                        
                        # Check if the corrected package.json is actually different
                        if corrected_package_json:
                            # Normalize both JSONs for comparison (parse and re-serialize to remove formatting differences)
                            try:
                                current_parsed = json.loads(current_package_json)
                                corrected_parsed = json.loads(corrected_package_json)
                                
                                # Compare the actual JSON content, not just string representation
                                if current_parsed != corrected_parsed:
                                    # Write corrected package.json
                                    with open(package_json_path, "w", encoding="utf-8") as f:
                                        f.write(corrected_package_json)
                                    
                                    print(f"[LocalRepo] ✅ Updated package.json to fix dependency errors")
                                    
                                    # Update regenerated_files with the correction
                                    relative_package_path = os.path.join(package_dir, "package.json") if package_dir != "." else "package.json"
                                    regenerated_files[relative_package_path] = {
                                        "old_code": current_package_json,
                                        "changes": f"LLM-corrected package.json to fix build dependency errors (attempt {attempt})",
                                        "updated_code": corrected_package_json
                                    }
                                    
                                    # Run npm install again with the updated package.json
                                    print(f"[LocalRepo] 📦 Running npm install after package.json update...")
                                    npm_install_success = run_npm_install_with_error_correction(
                                        package_dir_path, relative_package_path, repo_path, regenerated_files, pr_info
                                    )
                                    
                                    if npm_install_success:
                                        print(f"[LocalRepo] ✅ npm install succeeded after package.json update")
                                        
                                        # Retry build with updated dependencies
                                        print(f"[LocalRepo] 🔄 Retrying npm run build after dependency update...")
                                        result = attempt_npm_build(package_dir_path)
                                        
                                        if result is not None and result.returncode == 0:
                                            print(f"[LocalRepo] 🎉 npm run build succeeded after fixing dependencies!")
                                            build_results.append(f"{package_file}: SUCCESS (after dependency fix in attempt {attempt})")
                                            build_success = True
                                            break
                                        else:
                                            print(f"[LocalRepo] ⚠️ Build still failed after dependency fix, continuing with source code corrections...")
                                    else:
                                        print(f"[LocalRepo] ❌ npm install failed after package.json update")
                                else:
                                    print(f"[LocalRepo] ⚠️ Web search provided package.json but no meaningful changes detected")
                            except json.JSONDecodeError as e:
                                print(f"[LocalRepo] ❌ Error parsing JSON for comparison: {e}")
                                print(f"[LocalRepo] ⚠️ LLM didn't provide valid package.json correction")
                        else:
                            print(f"[LocalRepo] ⚠️ LLM didn't change package.json or correction failed")
                            
                    except Exception as e:
                        print(f"[LocalRepo] ❌ Error trying to fix package.json for dependency errors: {e}")
            
            # Extract affected files from the error message (for source code fixes)
            try:
                affected_files = asyncio.run(extract_affected_files_from_error_with_llm(full_build_error, package_dir_path, pr_info))
            except Exception as e:
                print(f"[LocalRepo] ❌ Error with LLM file identification, using fallback: {e}")
                return None
            
            if not affected_files:
                print(f"[LocalRepo] ⚠️ Could not identify specific files causing build errors")
                # Try to guess common problematic files
                common_files = [
                    'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json',  # TypeScript config files
                    'src/App.tsx', 'src/App.ts', 'src/index.tsx', 'src/index.ts'  # Source files
                ]
                for common_file in common_files:
                    if os.path.exists(os.path.join(package_dir_path, common_file)):
                        affected_files.append(common_file)
                        break
            
            if not affected_files:
                print(f"[LocalRepo] ❌ No files found to correct, skipping LLM correction")
                break
            
            print(f"[LocalRepo] 🎯 Identified affected files: {affected_files}")
            
            # Always use web search for build error correction
            try:
                corrected_files = asyncio.run(
                    fix_build_errors_with_llm(
                        full_build_error,
                        affected_files,
                        package_dir_path,
                        pr_info
                    )
                )
            except Exception as e:
                print(f"[LocalRepo] ❌ Error running LLM build correction: {e}")
                continue  # Try next iteration
            
            if not corrected_files:
                print(f"[LocalRepo] ❌ LLM could not provide valid corrections for attempt {attempt}")
                continue  # Try next iteration
            
            # Apply corrections to files
            files_changed = 0
            for file_path, corrected_content in corrected_files.items():
                full_file_path = os.path.join(package_dir_path, file_path)
                try:
                    # Read current content to check if LLM made changes
                    with open(full_file_path, "r", encoding="utf-8") as f:
                        current_content = f.read()
                    
                    if corrected_content.strip() == current_content.strip():
                        print(f"[LocalRepo] ⚠️ LLM returned same content for {file_path} (no changes)")
                        continue
                    
                    # Write corrected content
                    with open(full_file_path, "w", encoding="utf-8") as f:
                        f.write(corrected_content)
                    
                    # Update regenerated_files with the correction
                    relative_file_path = os.path.join(package_dir, file_path) if package_dir != "." else file_path
                    regenerated_files[relative_file_path] = {
                        "old_code": current_content,
                        "changes": f"LLM-corrected build errors (attempt {attempt})",
                        "updated_code": corrected_content
                    }
                    
                    files_changed += 1
                    print(f"[LocalRepo] ✅ Applied LLM correction to {file_path}")
                    
                except Exception as e:
                    print(f"[LocalRepo] ❌ Error writing corrected file {file_path}: {e}")
                    continue
            
            if files_changed == 0:
                print(f"[LocalRepo] ⚠️ No files were actually changed in attempt {attempt}")
                continue
            
            # Track this correction
            correction_history.append(f"Fixed {files_changed} files to resolve build errors")
            
            # Retry build with corrected files
            print(f"[LocalRepo] 🔄 Retrying npm run build with LLM corrections {attempt}...")
            result = attempt_npm_build(package_dir_path)
            
            if result is None:
                continue  # Try next iteration
            
            if result.returncode == 0:
                print(f"[LocalRepo] 🎉 npm run build succeeded after {attempt} LLM correction(s)!")
                build_results.append(f"{package_file}: SUCCESS (after {attempt} corrections)")
                build_success = True
                break
            
            # If we get here, this correction didn't work, continue to next attempt
            print(f"[LocalRepo] ⚠️ npm run build still failed after correction {attempt}, trying next iteration...")
        
        # Build loop completed
        if not build_success:
            print(f"[LocalRepo] ❌ npm run build failed after {MAX_BUILD_CORRECTION_ATTEMPTS} LLM correction attempts")
            build_results.append(f"{package_file}: FAILED (after {len(correction_history)} corrections)")
            if result is not None:
                print(f"[LocalRepo] 📄 Final build error output:")
                print(f"[LocalRepo] stdout: {result.stdout}")
                print(f"[LocalRepo] stderr: {result.stderr}")
    
    # Determine overall build status
    if not build_results:
        overall_status = "NO BUILDS RUN"
    elif all("SUCCESS" in result for result in build_results):
        overall_status = "ALL BUILDS SUCCESSFUL"
    elif any("SUCCESS" in result for result in build_results):
        overall_status = "PARTIAL SUCCESS"
    else:
        overall_status = "ALL BUILDS FAILED"
    
    print(f"[LocalRepo] 🏗️ Build validation summary:")
    for result in build_results:
        print(f"[LocalRepo]   - {result}")
    print(f"[LocalRepo] 🏗️ Overall build status: {overall_status}")
    
    # Add build status to regenerated_files metadata (for PR description)
    build_summary = {
        "status": overall_status,
        "details": build_results,
        "timestamp": "build_validation_with_correction_completed"
    }
    
    # Store build info in a special metadata entry
    regenerated_files["_build_validation_metadata"] = {
        "old_code": "",
        "changes": f"Build validation with LLM error correction: {overall_status}",
        "updated_code": json.dumps(build_summary, indent=2)
    }
    
    print(f"[LocalRepo] 🏗️ Build validation with error correction completed.")
    return overall_status

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
        
        # Generate lockfile if any package.json was changed (check for files ending with package.json)
        package_json_files_list = [f for f in regenerated_files.keys() if f.endswith("package.json")]
        package_json_files_dict = {f: regenerated_files[f] for f in package_json_files_list}
        
        if package_json_files_list:
            print(f"[LocalRepo] package.json files detected: {package_json_files_list}")
            
            # Process each package.json file
            for package_file in package_json_files_list:
                # Get the directory containing the package.json
                package_dir = os.path.dirname(package_file) if os.path.dirname(package_file) else "."
                package_dir_path = os.path.join(repo_path, package_dir)
                
                print(f"[LocalRepo] Running npm install in directory: {package_dir_path}")
                
                # Try npm install with intelligent error correction
                npm_success = run_npm_install_with_error_correction(
                    package_dir_path, package_file, repo_path, regenerated_files, pr_info
                )
        
        # Run npm build with error correction after all dependencies are resolved
        build_status = run_npm_build_with_error_correction(repo_path, package_json_files_dict, regenerated_files, pr_info)
        
        # TODO: Future user story - Generate and run tests here
        # print("[LocalRepo] Preparing for test generation and execution...")
        # test_files = generate_test_cases(repo_path, regenerated_files)
        # regenerated_files.update(test_files)
        # 
        # # Run tests with Jest, Selenium, Cucumber
        # run_jest_tests(repo_path)
        # run_selenium_tests(repo_path)
        # run_cucumber_tests(repo_path)
        
        print(f"[LocalRepo] ✅ Local processing completed with intelligent error correction!")
        print(f"[LocalRepo] 📊 Final summary:")
        print(f"[LocalRepo]   - Total files: {len(regenerated_files)}")
        print(f"[LocalRepo]   - Package.json files processed: {len(package_json_files_list)}")
        
        print(f"[LocalRepo]   - Build status: {build_status}")
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
    print(f"Branch: {pr['head']['ref']}")  # type: ignore
    print("Processing all files in the PR")

    files_for_update = collect_files_for_refinement(REPO_NAME, PR_NUMBER, pr_info)
    print(f"Files selected for refinement: {list(files_for_update.keys())}")

    if not files_for_update:
        print("No files found for refinement. Exiting.")
        return None

    requirements_text = fetch_requirements_from_readme(REPO_NAME, pr['head']['ref'])  # type: ignore
    print(f"Requirements from README.md:\n{'-'*60}\n{requirements_text}\n{'-'*60}")

    # Run AI refinement with web search integration
    print(f"[Step3] 🌐 Starting AI refinement with real-time web search integration...")
    regenerated_files = asyncio.run(regenerate_code_with_mcp(files_for_update, requirements_text, pr, pr_info))
    
    # Process files locally (clone repo, apply changes, generate lockfile with web search error correction)
    print(f"[Step3] 🏗️ Starting local processing with web search error correction...")
    regenerated_files = process_pr_with_local_repo(pr_info, regenerated_files)
    
    print(f"[Step3] ✅ Complete AI regeneration pipeline finished!")
    print(f"[Step3] 📊 Files processed: {len(regenerated_files)}")
    
    return regenerated_files

