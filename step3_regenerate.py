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

from extract_dependencies import (
    extract_external_dependencies,
    parse_file_dependencies,
    
    )
from llm_response_extraction import (
    extract_response_content,
    extract_changes,
    extract_updated_code,
    cleanup_extracted_code
    )

load_dotenv()
# OpenAI for web search (code generation + error correction)
try:
    import openai
    from openai import OpenAI
    OPENAI_CLIENT = None
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if OPENAI_API_KEY:
        OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY)
        print(f"[DEBUG] ✅ OpenAI client initialized for web search (code generation + error correction)")
    else:
        print(f"[DEBUG] ⚠️ OPENAI_API_KEY not found - web search disabled")
except ImportError:
    print(f"[DEBUG] ⚠️ OpenAI package not installed - web search disabled")
    OPENAI_CLIENT = None
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

# Project type detection
def detect_project_type(directory_path: str) -> str:
    """
    Detect if a directory contains a React/Node.js project or Spring Boot/Maven project.
    Returns: 'react', 'springboot', 'mixed', or 'unknown'
    """
    try:
        has_package_json = os.path.exists(os.path.join(directory_path, 'package.json'))
        has_pom_xml = os.path.exists(os.path.join(directory_path, 'pom.xml'))
        
        if has_package_json and has_pom_xml:
            return 'mixed'
        elif has_package_json:
            return 'react'
        elif has_pom_xml:
            return 'springboot'
        else:
            return 'unknown'
    except Exception as e:
        print(f"[Step3] ⚠️ Error detecting project type for {directory_path}: {e}")
        return 'unknown'

def detect_project_types_in_repo(repo_path: str) -> Dict[str, str]:
    """
    Detect project types in all subdirectories of a repository.
    Returns dict mapping directory paths to project types.
    """
    project_types = {}
    
    try:
        for root, dirs, files in os.walk(repo_path):
            # Skip node_modules and target directories
            dirs[:] = [d for d in dirs if d not in ['node_modules', 'target', '.git']]
            
            project_type = detect_project_type(root)
            if project_type != 'unknown':
                rel_path = os.path.relpath(root, repo_path)
                if rel_path == '.':
                    rel_path = ''
                project_types[rel_path] = project_type
                print(f"[Step3] 📁 Detected {project_type} project in: {rel_path or 'root'}")
    
    except Exception as e:
        print(f"[Step3] ⚠️ Error scanning repository for project types: {e}")
    
    return project_types


def update_external_dependency_store(file_path: str, external_deps: Set[str]):
    """Update the global external dependency store with dependencies from a file"""
    if external_deps:
        EXTERNAL_DEPENDENCY_STORE[file_path] = external_deps
        print(f"[Step3] 📦 Tracked {len(external_deps)} external dependencies from {file_path}: {sorted(external_deps)}")
    else:
        print(f"[Step3] 📦 No external dependencies found in {file_path}")

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
    is_pom_xml = file_name.endswith('pom.xml')
    is_java_file = file_extension in ['java', 'kt']

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
    elif is_java_file:
        dependency_instructions = (
            f"\n---\n🟤 **IMPROVEMENT-FOCUSED JAVA/KOTLIN HANDLING:**\n"
            f"For Java/Kotlin files, make improvements and fix issues:\n\n"
            f"1. **FIX ERRORS AND IMPROVE:**\n"
            f"   - Fix syntax errors, missing imports, or type issues\n"
            f"   - Remove unused imports or variables that cause warnings\n"
            f"   - Add missing imports for undefined classes\n"
            f"   - Fix incorrect package or import statements\n"
            f"   - Improve code structure and readability\n"
            f"   - Apply modern patterns and best practices\n\n"
            f"2. **IMPROVEMENTS TO CONSIDER:**\n"
            f"   - Refactor for better organization and clarity\n"
            f"   - Improve error handling and edge cases\n"
            f"   - Apply modern Java/Kotlin patterns when beneficial\n"
            f"   - Optimize performance where appropriate\n"
            f"   - Enhance type safety and validation\n\n"
            f"3. **PREVENT BUILD ERRORS AND IMPROVE QUALITY:**\n"
            f"   - Remove unused imports that cause build warnings\n"
            f"   - Add missing imports for undefined classes\n"
            f"   - Fix type errors and obvious bugs\n"
            f"   - Correct package or import statements if they're wrong\n"
            f"   - Improve code maintainability and readability\n\n"
            f"🟤 PHILOSOPHY: Fix errors and improve code quality while maintaining functionality\n"
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

async def process_single_file_with_web_search(file_name: str, old_code: str, requirements: str, pr_info: Optional[dict] = None, dynamic_context_cache: Optional[Dict[str, str]] = None, pr_files: Optional[Set[str]] = None, processed_files: Optional[Set[str]] = None) -> dict:
    """Process a single file through AI refinement pipeline using OpenAI with web search for latest practices"""
    try:
        print(f"[Step3] 🌐 Processing file with WEB SEARCH: {file_name}")
        
        if not OPENAI_CLIENT:
            print(f"[Step3] ⚠️ OpenAI client not available - falling back to regular MCP")
            # We need to use MCP with a session, so this function should only be called when OpenAI is available
            # For fallback, the main function should call process_single_file directly
            raise Exception("OpenAI client not available - use regular MCP process_single_file instead")
        
        # Fetch context using dynamic cache with dependency optimization
        if dynamic_context_cache is not None and pr_files is not None:
            print(f"[Step3] Using DEPENDENCY-OPTIMIZED dynamic context for {file_name}")
            context = fetch_dynamic_context(file_name, dynamic_context_cache, pr_files, processed_files)
           
        
        # Create web search enhanced prompt
        web_search_prompt = compose_web_search_prompt(requirements, old_code, file_name, context)
        
        print(f"[Step3] 🌐 Calling OpenAI with web search for {file_name}...")
        
        try:
            # Use OpenAI Responses API with web search
            response = await asyncio.to_thread(
                OPENAI_CLIENT.responses.create,
                model="gpt-4.1-mini",
                tools=[{"type": "web_search_preview"}],
                input=web_search_prompt
            )
            
            print(f"[Step3] 🌐 OpenAI web search call completed for {file_name}")
            
            # Extract the response text from Responses API
            if hasattr(response, 'output_text'):
                response_text = response.output_text
            else:
                print(f"[Step3] ❌ Could not extract response from OpenAI web search")
                response_text = ""
            
            # Extract changes and updated code from web search response
            changes = extract_changes(response_text, file_name)
            updated_code = extract_updated_code(response_text)
            updated_code = cleanup_extracted_code(updated_code)
            
            # Fallback if no updated code found
            if not updated_code:
                print(f"⚠️ WARNING: Could not extract updated code from web search for {file_name}. Using original code.")
                updated_code = old_code
            
            print(f"[Step3] ✅ Successfully processed {file_name} with web search")
            
            # Track external dependencies from the processed file
            external_deps = extract_external_dependencies(file_name, updated_code)
            update_external_dependency_store(file_name, external_deps)
            
            return {
                "old_code": old_code,
                "changes": changes,
                "updated_code": updated_code,
                "token_usage": (0, 0, 0)  # Web search doesn't provide token usage
            }
            
        except Exception as e:
            print(f"[Step3] ❌ Error with OpenAI web search for {file_name}: {e}")
            print(f"[Step3] 🔄 Web search failed - returning original code")
            # Return original code if web search fails
            return {
                "old_code": old_code,
                "changes": f"Web search failed: {str(e)}",
                "updated_code": old_code,
                "token_usage": (0, 0, 0)
            }
        
    except Exception as e:
        print(f"[Step3] Error processing file {file_name} with web search: {e}")
        return {
            "old_code": old_code,
            "changes": f"Error during web search processing: {str(e)}",
            "updated_code": old_code,
            "token_usage": (0, 0, 0)
        }

def compose_web_search_prompt(requirements: str, code: str, file_name: str, context: str) -> str:
    """Create a web search enhanced prompt for conservative code correction with error prevention"""
    # Get the file extension for the AI to understand the language
    file_extension = file_name.split('.')[-1].lower()
    
    # Check if this is a package.json file for special dependency handling
    is_package_json = file_name.endswith('package.json')
    
    base_prompt = (
        f"You are an expert AI code reviewer with access to real-time web search. Your job is to improve the given file `{file_name}` "
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
    
    web_search_instructions = (
        f"\n---\n🌐 **WEB SEARCH FOR IMPROVEMENTS AND ERROR PREVENTION:**\n"
        f"Use web search to identify errors, improvements, and best practices:\n\n"
        f"1. **SEARCH for common build errors** with the specific technology/framework\n"
        f"2. **VERIFY syntax issues** and compatibility problems\n"
        f"3. **CHECK for deprecated APIs** and modern alternatives\n"
        f"4. **FIND breaking changes** in dependencies that affect this code\n"
        f"5. **LOOK UP security vulnerabilities** that need immediate fixes\n"
        f"6. **RESEARCH import/export issues** for the specific library versions\n"
        f"7. **SEARCH for performance optimizations** and best practices\n"
        f"8. **LOOK UP modern patterns** and improved coding approaches\n\n"
        f"🔍 **IMPROVEMENT-FOCUSED SEARCH STRATEGY:**\n"
        f"- Search for '{file_extension} common errors' or 'build errors'\n"
        f"- Search for specific error messages if code has issues\n"
        f"- Search for 'deprecated' + library name\n"
        f"- Search for 'security vulnerabilities' + library name\n"
        f"- Search for 'performance optimization' + technology name\n"
        f"- Search for 'best practices' + technology name\n"
        f"- Search for 'modern patterns' + technology name\n"
        f"- Verify import syntax for current versions\n\n"
        f"🎯 **PRIORITIZE FIXES AND IMPROVEMENTS:**\n"
        f"- **SYNTAX ERRORS** - fix code that won't compile/run\n"
        f"- **MISSING IMPORTS** - add imports for undefined variables\n"
        f"- **DEPRECATED APIS** - replace with modern alternatives\n"
        f"- **SECURITY ISSUES** - fix vulnerabilities\n"
        f"- **BUILD ERRORS** - resolve compilation failures\n"
        f"- **TYPE ERRORS** - fix TypeScript compilation issues\n"
        f"- **PERFORMANCE** - optimize slow operations\n"
        f"- **CODE QUALITY** - improve readability and maintainability\n"
        f"- **BEST PRACTICES** - apply modern patterns and conventions\n"
        f"- **STRUCTURE** - refactor for better organization\n\n"
        f"✅ **ENCOURAGED IMPROVEMENTS:**\n"
        f"- Performance optimizations where beneficial\n"
        f"- Code quality and readability improvements\n"
        f"- Modern patterns and best practices\n"
        f"- Better error handling and edge cases\n"
        f"- Improved type safety and validation\n"
        f"- Cleaner code structure and organization\n"
    )
    
    if is_package_json:
        dependency_instructions = (
            f"\n---\n🔍 **IMPROVEMENT-FOCUSED PACKAGE.JSON WEB SEARCH:**\n"
            f"This is a package.json file. Use web search to identify issues and improvements:\n\n"
            f"1. **SEARCH for ISSUES AND IMPROVEMENTS:**\n"
            f"   - Security vulnerabilities that need fixes\n"
            f"   - Breaking changes causing build failures\n"
            f"   - Deprecated packages and modern alternatives\n"
            f"   - Version conflicts preventing installation\n"
            f"   - Performance improvements and optimizations\n"
            f"   - Latest stable versions with better features\n\n"
            f"2. **VERIFY what's actually imported** in the context files\n"
            f"   - Remove dependencies that are clearly unused\n"
            f"   - Add dependencies that are imported but missing\n"
            f"   - Update to better versions when beneficial\n"
            f"   - Consider modern alternatives for deprecated packages\n\n"
            f"3. **IMPROVEMENT-FOCUSED SEARCH QUERIES:**\n"
            f"   - '[package-name] security vulnerabilities'\n"
            f"   - '[package-name] deprecated breaking changes'\n"
            f"   - '[package-name] build errors'\n"
            f"   - '[package-name] latest version features'\n"
            f"   - '[package-name] performance improvements'\n"
            f"   - '[package-name] modern alternatives'\n"
            f"   - 'npm install errors [package-name]'\n\n"
            f"✅ **ENCOURAGED IMPROVEMENTS:**\n"
            f"   - Update to latest stable versions when beneficial\n"
            f"   - Replace deprecated packages with modern alternatives\n"
            f"   - Add performance-optimized packages\n"
            f"   - Improve dependency organization and structure\n"
            f"   - Remove unused dependencies to reduce bundle size\n\n"
            f"❌ **AVOID:**\n"
            f"   - Adding unnecessary new dependencies\n"
            f"   - Breaking changes without clear benefits\n"
            f"   - Experimental or unstable versions\n"
            f"   - Over-engineering the dependency structure\n\n"
            f"💡 **PHILOSOPHY**: Improve package.json for better performance and maintainability\n"
            f"- Fix actual errors and security issues\n"
            f"- Update to better versions when beneficial\n"
            f"- Optimize for performance and bundle size\n"
            f"- Maintain compatibility while improving quality\n"
        )
    else:
        dependency_instructions = (
            f"\n---\n📦 **IMPROVEMENT-FOCUSED DEPENDENCY WEB SEARCH:**\n"
            f"For import statements and dependencies:\n"
            f"1. **SEARCH for ISSUES AND IMPROVEMENTS:**\n"
            f"   - Syntax errors in imports\n"
            f"   - Deprecated imports and modern alternatives\n"
            f"   - Missing imports for undefined variables\n"
            f"   - Incorrect import paths\n"
            f"   - Performance-optimized import patterns\n"
            f"   - Modern import/export best practices\n\n"
            f"2. **VERIFY AND IMPROVE:**\n"
            f"   - Check if imports are causing build failures\n"
            f"   - Look up breaking changes and modern alternatives\n"
            f"   - Search for compatibility issues and improvements\n"
            f"   - Find better import patterns and optimizations\n\n"
            f"🚀 **IMPROVEMENT-FOCUSED SEARCH:**\n"
            f"1. **Search for specific error messages** if code has issues\n"
            f"2. **Check for deprecated APIs** and modern alternatives\n"
            f"3. **Find import/export problems** and best practices\n"
            f"4. **Look up TypeScript errors** and type improvements\n"
            f"5. **Search for performance optimizations** in imports\n"
            f"6. **Find modern patterns** and better coding approaches\n\n"
            f"✅ **ENCOURAGED IMPROVEMENTS:**\n"
            f"- Modern import/export patterns\n"
            f"- Performance optimizations in imports\n"
            f"- Better type safety and validation\n"
            f"- Cleaner import organization\n"
            f"- Modern alternatives to deprecated APIs\n"
            f"- Best practices for the specific technology\n"
        )
    
    format_instructions = (
        f"\n---\nPlease return the updated code and changes in the following EXACT format:\n"
        f"### Changes:\n- A clean bullet-point summary of fixes and improvements.\n\n"
        f"### Updated Code:\n```{file_extension}\n<ONLY THE IMPROVED CODE HERE>\n```\n\n"
        f"⚠️ **CRITICAL REQUIREMENTS:**\n"
        f"1. **IMPROVEMENT-FOCUSED APPROACH**: Make changes that fix problems and improve code quality\n"
        f"2. **BALANCED REFACTORING**: Improve working code while maintaining functionality\n"
        f"3. **COMPREHENSIVE IMPROVEMENTS**: Fix errors, improve performance, apply best practices\n"
        f"4. **USE WEB SEARCH** to find improvements, best practices, and modern patterns\n"
        f"5. **DO NOT include URLs, links, or citations** in the changes section\n"
        f"6. Do NOT use <think> tags or any other XML-like tags\n"
        f"7. Provide bullet-point summary of changes under the `### Changes` heading\n"
        f"8. Provide ONLY ONE code block under the `### Updated Code` heading\n"
        f"9. Do NOT show the old code again in your response\n"
        f"10. Do NOT suggest creating new files. Only update this file\n"
        f"11. The response must start with `### Changes:` and end with the code block\n"
        f"12. Return improved code with better quality, performance, and maintainability\n"
        f"13. Return the SAME TYPE of code as the original file ({file_extension})\n"
        f"14. **IF NO IMPROVEMENTS ARE NEEDED:**\n"
        f"    - In the ### Changes section, write: 'No improvements needed.'\n"
        f"    - In the ### Updated Code section, return the original code unchanged.\n"
        f"15. **CHANGES FORMAT**: Use simple, clean bullet points like:\n"
        f"    - Fixed syntax error in import statement\n"
        f"    - Added missing import for undefined variable\n"
        f"    - Optimized performance with better algorithm\n"
        f"    - Applied modern patterns and best practices\n"
        f"    - Improved code readability and maintainability\n"
        f"    - Enhanced type safety and error handling\n"
        f"16. **CHANGES SHOULD BE MEANINGFUL**: Report both fixes and improvements\n"
    )
    
    return base_prompt + web_search_instructions + dependency_instructions + format_instructions


async def regenerate_code_with_mcp(files: Dict[str, str], requirements: str, pr, pr_info=None) -> Dict[str, Dict[str, str]]:
    """Main function to regenerate code using MCP with dynamic context updates"""
    regenerated = {}
    server_params = StdioServerParameters(command="python", args=["server.py"])

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
                    
                    # Determine processing approach based on file type
                    file_extension = file_name.split('.')[-1].lower()
                    is_java_file = file_extension in ['java', 'kt']
                    is_pom_xml = file_name.endswith('pom.xml')
                    
                    # Use conservative approach for Java/Maven files, web search for others
                    if is_java_file or is_pom_xml:
                        print(f"[Step3] 🟤 Using conservative LLM approach for {file_name}")
                        # Use the conservative compose_prompt function
                        context = fetch_dynamic_context(file_name, dynamic_context_cache, pr_files, processed_files)
                        prompt = compose_prompt(requirements, old_code, file_name, context)
                        
                        # Call MCP with conservative prompt
                        result = await session.call_tool("codegen", arguments={"prompt": prompt})
                        
                        # Extract and process the result
                        response_content = extract_response_content(result, file_name)
                        changes = extract_changes(response_content, file_name)
                        updated_code = extract_updated_code(response_content)
                        updated_code = cleanup_extracted_code(updated_code)
                        
                        file_result = {
                            "old_code": old_code,
                            "changes": changes,
                            "updated_code": updated_code,
                        }
                    else:
                        # Use web search for React/JavaScript/TypeScript files
                        if OPENAI_CLIENT:
                            print(f"[Step3] 🌐 Using web search for code generation: {file_name}")
                            file_result = await process_single_file_with_web_search(
                                file_name, 
                                old_code, 
                                requirements, 
                                pr_info,
                                dynamic_context_cache,
                                pr_files,
                                processed_files)
                        else:
                            print(f"[Step3] ⚠️ OpenAI not available, using MCP fallback for {file_name}")
                            # Fallback to MCP without web search
                            context = fetch_dynamic_context(file_name, dynamic_context_cache, pr_files, processed_files)
                            prompt = compose_prompt(requirements, old_code, file_name, context)
                            
                            result = await session.call_tool("codegen", arguments={"prompt": prompt})
                            
                            response_content = extract_response_content(result, file_name)
                            changes = extract_changes(response_content, file_name)
                            updated_code = extract_updated_code(response_content)
                            updated_code = cleanup_extracted_code(updated_code)
                            

                            
                            file_result = {
                                "old_code": old_code,
                                "changes": changes,
                                "updated_code": updated_code,
                            }
                    
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

        # Print final summary with hybrid approach (web search for React, conservative for Maven)
        print(f"[Step3] 🎉 AI processing completed with HYBRID APPROACH!")
        print(f"[Step3] 📊 Processing Summary:")
        print(f"[Step3]   - Total files processed: {len(regenerated)}")
        print(f"[Step3]   - Regular files refined: {len(regular_files)}")
        print(f"[Step3]   - Package.json files analyzed: {len(package_json_files)}")
        print(f"[Step3]   - React/JS/TS files: {'🌐 Web search enabled' if OPENAI_CLIENT else '⚠️ MCP fallback'}")
        print(f"[Step3]   - External dependencies tracked: {len(EXTERNAL_DEPENDENCY_STORE)} files contributed to dependency analysis")

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


async def fix_package_json_with_web_search(package_json_content, npm_error, package_file_path, pr_info):
    """
    Use OpenAI with web search to fix package.json based on npm install errors.
    This function uses web search to find current package versions and fix version conflicts.
    Returns corrected package.json content or None if correction fails.
    """
    if not OPENAI_CLIENT:
        print("[LocalRepo] ⚠️ OpenAI client not available - falling back to regular LLM")
        return None
    
    if not pr_info:
        print("[LocalRepo] 🔧 No PR info available for web search error correction")
        return None
    
    print(f"[LocalRepo] 🌐 Using OpenAI with web search to fix package.json errors...")
    
    # Always use web search for npm install errors (default behavior)
    print(f"[LocalRepo] 🌐 Using web search for npm install errors (latest practices enabled)...")
    
    # Create web search prompt for npm install errors
    web_search_prompt = f"""I'm getting npm install errors related to package versions. Here's the exact error:

{npm_error}

Current package.json:
```json
{package_json_content}
```

Please search for the current available versions of the failing packages and help me fix the version constraints. I need:

1. What are the current stable/LTS versions of the failing packages?
2. Which version ranges actually exist and work together?
3. How to fix ETARGET/version not found errors?
4. Compatible version combinations for the failing packages

Please provide a corrected package.json with working version constraints based on what's currently available."""

    try:
        # Use OpenAI Responses API with web search
        response = await asyncio.to_thread(
            OPENAI_CLIENT.responses.create,
            model="gpt-4.1-mini",
            tools=[{"type": "web_search_preview"}],
            input=web_search_prompt
        )
        
        # Extract the response text from Responses API
        if hasattr(response, 'output_text'):
            response_text = response.output_text
        else:
            print("[LocalRepo] ❌ Could not extract response from OpenAI web search")
            return None
        
        print(f"[LocalRepo] 🌐 Web search completed, analyzing response...")
        
        # Parse the corrected package.json from the response
        patterns = [
            # Pattern for explicit package.json mentions with JSON
            r'```json\s*//\s*package\.json[^\n]*\n([\s\S]*?)```',
            r'```json\s*package\.json[^\n]*\n([\s\S]*?)```',
            # Pattern for corrected/fixed package.json
            r'(?:corrected|fixed|updated)\s+package\.json[:\s]*```json\n([\s\S]*?)```',
            # Generic JSON pattern (most common)
            r'```json\n([\s\S]*?)```'
        ]
        
        corrected_json = None
        for pattern in patterns:
            matches = re.findall(pattern, response_text, re.IGNORECASE)
            
            for match in matches:
                potential_json = match.strip() if isinstance(match, str) else match[0].strip()
                
                # Validate JSON
                try:
                    parsed = json.loads(potential_json)
                    # Check if it looks like a package.json (has name, dependencies, etc.)
                    if any(key in parsed for key in ['dependencies', 'devDependencies', 'name', 'scripts']):
                        corrected_json = potential_json
                        break
                except json.JSONDecodeError:
                    continue  # Skip invalid JSON
            
            if corrected_json:
                break  # Found valid package.json, stop trying other patterns
        
        if corrected_json:
            print(f"[LocalRepo] 🎉 Web search successfully provided package.json correction")
            return corrected_json
        else:
            print(f"[LocalRepo] ❌ Could not extract corrected package.json from web search response")
            print(f"[LocalRepo] 🔍 Response preview: {response_text[:500]}...")
            # Fall back to regular LLM if web search didn't provide parseable results
            print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
            return None
            
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during web search package.json correction: {e}")
        print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
        return None

async def fix_package_json_for_build_errors_with_web_search(package_json_content, build_error, package_dir_path, pr_info):
    """
    Use OpenAI with web search to fix package.json based on build errors that indicate missing dependencies.
    This function uses web search to find current package versions and resolve dependency issues.
    Returns corrected package.json content or None if correction fails.
    """
    if not OPENAI_CLIENT:
        print("[LocalRepo] ⚠️ OpenAI client not available - falling back to regular LLM")
        return None
    
    if not pr_info:
        print("[LocalRepo] 🔧 No PR info available for web search build dependency correction")
        return None
    
    print(f"[LocalRepo] 🌐 Using OpenAI with web search to fix package.json for build dependency errors...")
    
    # Always use web search for build dependency errors (default behavior)
    print(f"[LocalRepo] 🌐 Using web search for build dependency errors (latest practices enabled)...")
    
    # Create web search prompt for build dependency errors
    web_search_prompt = f"""I'm getting build errors indicating missing dependencies. Here's the exact error:

{build_error}

Current package.json:
```json
{package_json_content}
```

Please search for the current available packages and help me fix the missing dependencies. I need:

1. What are the correct package names and current stable versions for the missing modules?
2. Which packages need to be in dependencies vs devDependencies?
3. What @types/* packages are needed for TypeScript projects?
4. Are there any package name changes or deprecations I should know about?

Please provide a corrected package.json with the missing dependencies added based on what's currently available."""

    try:
        # Use OpenAI Responses API with web search
        response = await asyncio.to_thread(
            OPENAI_CLIENT.responses.create,
            model="gpt-4.1-mini",
            tools=[{"type": "web_search_preview"}],
            input=web_search_prompt
        )
        
        # Extract the response text from Responses API
        if hasattr(response, 'output_text'):
            response_text = response.output_text
        else:
            print("[LocalRepo] ❌ Could not extract response from OpenAI web search")
            return None
        
        print(f"[LocalRepo] 🌐 Web search completed, analyzing response...")
        
        # Parse the corrected package.json from the response
        patterns = [
            # Pattern for explicit package.json mentions with JSON
            r'```json\s*//\s*package\.json[^\n]*\n([\s\S]*?)```',
            r'```json\s*package\.json[^\n]*\n([\s\S]*?)```',
            # Pattern for corrected/fixed package.json
            r'(?:corrected|fixed|updated)\s+package\.json[:\s]*```json\n([\s\S]*?)```',
            # Generic JSON pattern (most common)
            r'```json\n([\s\S]*?)```'
        ]
        
        corrected_json = None
        for pattern in patterns:
            matches = re.findall(pattern, response_text, re.IGNORECASE)
            
            for match in matches:
                potential_json = match.strip() if isinstance(match, str) else match[0].strip()
                
                # Validate JSON
                try:
                    parsed = json.loads(potential_json)
                    # Check if it looks like a package.json (has name, dependencies, etc.)
                    if any(key in parsed for key in ['dependencies', 'devDependencies', 'name', 'scripts']):
                        corrected_json = potential_json
                        break
                except json.JSONDecodeError:
                    continue  # Skip invalid JSON
            
            if corrected_json:
                break  # Found valid package.json, stop trying other patterns
        
        if corrected_json:
            print(f"[LocalRepo] 🎉 Web search successfully provided package.json correction for build dependencies")
            return corrected_json
        else:
            print(f"[LocalRepo] ❌ Could not extract corrected package.json from web search response")
            print(f"[LocalRepo] 🔍 Response preview: {response_text[:500]}...")
            # Fall back to regular LLM if web search didn't provide parseable results
            print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
            return None
            
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during web search package.json build dependency correction: {e}")
        print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
        return None

async def fix_build_errors_with_web_search(build_error, affected_files, package_dir_path, pr_info):
    """
    Use OpenAI with web search to fix TypeScript/build configuration errors.
    This function specifically targets build configuration issues that benefit from web search.
    Returns dict of corrected files or None if correction fails.
    """
    if not OPENAI_CLIENT:
        print("[LocalRepo] ⚠️ OpenAI client not available - falling back to regular LLM")
        return None
    
    if not pr_info:
        print("[LocalRepo] 🔧 No PR info available for web search build error correction")
        return None
    
    print(f"[LocalRepo] 🌐 Using OpenAI with web search to fix build errors...")
    
    # Always use web search for build errors (default behavior)
    print(f"[LocalRepo] 🌐 Using web search for build errors (latest practices enabled)...")
    
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
        print("[LocalRepo] ❌ No affected files could be read for web search build error correction")
        return None
    
    # Create build error correction prompt for web search
    files_context = ""
    for file_path, content in affected_file_contents.items():
        files_context += f"\n\n--- {file_path} ---\n```json\n{content}\n```"
    
    web_search_prompt = f"""I'm getting build errors and need help fixing them. Here are the exact errors:

{build_error}

Current files that need fixing:
{files_context}

Please search for the latest information and help me fix these specific errors. I need:
1. Current API and syntax for the packages/libraries involved
2. Proper TypeScript/JavaScript usage patterns
3. How to fix import/export errors and missing members
4. Compatible version information and breaking changes
5. Modern best practices for the technologies involved

Please provide the corrected files with proper imports, exports, and syntax."""

    try:
        # Use OpenAI Responses API with web search
        response = await asyncio.to_thread(
            OPENAI_CLIENT.responses.create,
            model="gpt-4.1-mini",
            tools=[{"type": "web_search_preview"}],
            input=web_search_prompt
        )
        
        # Extract the response text from Responses API
        if hasattr(response, 'output_text'):
            response_text = response.output_text
        else:
            print("[LocalRepo] ❌ Could not extract response from OpenAI web search")
            return None
        
        print(f"[LocalRepo] 🌐 Web search completed, analyzing response...")
        
        # Parse the corrected files from the response
        corrected_files = {}
        
        # Enhanced patterns for extracting TypeScript config files
        patterns = [
            # Pattern for JSON config files with explicit filenames
            r'```json\s*//\s*([^\n]*\.json)[^\n]*\n([\s\S]*?)```',
            r'```json\s*([^\n]*\.json)[^\n]*\n([\s\S]*?)```',
            # Pattern for config files mentioned by name
            r'(tsconfig\.(?:app|node)?\.json)\s*[:\n]+\s*```[a-zA-Z0-9]*\n([\s\S]*?)```',
            # Pattern for any JSON code block near mentions of config files
            r'(?:tsconfig\.(?:app|node)?\.json|TypeScript configuration)[\s\S]*?```json\n([\s\S]*?)```',
            # Generic pattern for JSON blocks
            r'```json\n([\s\S]*?)```'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, response_text, re.IGNORECASE)
            
            for match in matches:
                if len(match) == 2:  # (filename, content)
                    file_path, file_content = match
                    file_path = file_path.strip()
                    file_content = file_content.strip()
                else:  # Just content
                    file_content = match if isinstance(match, str) else match[0]
                    file_content = file_content.strip()
                    # Try to guess which config file this is for
                    if 'app' in file_content.lower() or '"app"' in file_content:
                        file_path = 'tsconfig.app.json'
                    elif 'node' in file_content.lower() or '"node"' in file_content:
                        file_path = 'tsconfig.node.json'
                    else:
                        file_path = 'tsconfig.json'
                
                # Validate JSON
                try:
                    json.loads(file_content)
                    # Check if this file is one we're trying to fix
                    if file_path in affected_file_contents:
                        corrected_files[file_path] = file_content
                        print(f"[LocalRepo] ✅ Web search provided correction for {file_path}")
                    elif file_path in [f for f in affected_files]:
                        corrected_files[file_path] = file_content
                        print(f"[LocalRepo] ✅ Web search provided correction for {file_path}")
                except json.JSONDecodeError:
                    continue  # Skip invalid JSON
            
            if corrected_files:
                break  # Found files with this pattern, stop trying others
        
        if corrected_files:
            print(f"[LocalRepo] 🎉 Web search successfully provided {len(corrected_files)} corrections")
            return corrected_files
        else:
            print(f"[LocalRepo] ❌ Could not extract corrected files from web search response")
            print(f"[LocalRepo] 🔍 Response preview: {response_text[:500]}...")
            # Fall back to regular LLM if web search didn't provide parseable results
            print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
            return None
            
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during web search build error correction: {e}")
        print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
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
        
        # Always use web search for error correction
        try:
            corrected_package_json = asyncio.run(
                fix_package_json_with_web_search(
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
                for file in files:
                    if file.endswith(('.ts', '.tsx', '.js', '.jsx', '.vue', '.json', '.css', '.scss', '.html')):
                        rel_path = os.path.relpath(os.path.join(root, file), package_dir_path)
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

Return ONLY a JSON array of file paths, relative to the project root:
["file1.ts", "src/file2.tsx", "tsconfig.json"]

Do not include any explanation, just the JSON array."""

        try:
            if not OPENAI_CLIENT:
                print(f"[LocalRepo] ⚠️ OpenAI not available for file identification, using fallback")
                return None
            
            # Use OpenAI to identify files
            response = await asyncio.to_thread(
                OPENAI_CLIENT.chat.completions.create,
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": file_identification_prompt}],
                temperature=0.1
            )
            
            response_text = response.choices[0].message.content
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
                    # Filter to only files that actually exist
                    existing_files = []
                    for file_path in affected_files:
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
            print(f"[LocalRepo] ❌ Error with LLM file identification: {e}")
            return None


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
        
        # Detect project types in the repository
        project_types = detect_project_types_in_repo(repo_path)
        print(f"[LocalRepo] 📁 Detected project types: {project_types}")
        
        # Process React/Node.js projects
        package_json_files_list = [f for f in regenerated_files.keys() if f.endswith("package.json")]
        package_json_files_dict = {f: regenerated_files[f] for f in package_json_files_list}
        
        if package_json_files_list:
            print(f"[LocalRepo] 📦 package.json files detected: {package_json_files_list}")
            
            # Process each package.json file
            for package_file in package_json_files_list:
                # Get the directory containing the package.json
                package_dir = os.path.dirname(package_file) if os.path.dirname(package_file) else "."
                package_dir_path = os.path.join(repo_path, package_dir)
                
                print(f"[LocalRepo] 📦 Running npm install in directory: {package_dir_path}")
                
                # Try npm install with intelligent error correction
                npm_success = run_npm_install_with_error_correction(
                    package_dir_path, package_file, repo_path, regenerated_files, pr_info
                )
        
        # Process Maven/Spring Boot projects
        pom_xml_files_list = [f for f in regenerated_files.keys() if f.endswith("pom.xml")]
        pom_xml_files_dict = {f: regenerated_files[f] for f in pom_xml_files_list}
        
        if pom_xml_files_list:
            print(f"[LocalRepo] 🏗️ pom.xml files detected: {pom_xml_files_list}")
            
            # Process each pom.xml file
            for pom_file in pom_xml_files_list:
                # Get the directory containing the pom.xml
                pom_dir = os.path.dirname(pom_file) if os.path.dirname(pom_file) else "."
                pom_dir_path = os.path.join(repo_path, pom_dir)
                
                print(f"[LocalRepo] 🏗️ Running mvn clean install in directory: {pom_dir_path}")
                
                # Try mvn install with intelligent error correction
                mvn_success = run_mvn_install_with_error_correction(
                    pom_dir_path, pom_file, repo_path, regenerated_files, pr_info
                )
        
        # Run builds for both project types
        react_build_status = "SKIPPED - No React projects"
        
        if package_json_files_list:
            print(f"[LocalRepo] 🏗️ Running npm build validation for React projects...")
            react_build_status = fix_build_errors_with_web_search(repo_path, package_json_files_dict, regenerated_files, pr_info)
        
        
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
        print(f"[LocalRepo]   - Pom.xml files processed: {len(pom_xml_files_list)}")
        
        # Count LLM corrections with more detail
        llm_corrected_files = [f for f in regenerated_files.values() if 'LLM-corrected' in f.get('changes', '') or 'LLM attempted' in f.get('changes', '')]
        successful_corrections = [f for f in llm_corrected_files if 'npm install still failed' not in f.get('changes', '') and 'mvn clean install still failed' not in f.get('changes', '')]
        failed_corrections = [f for f in llm_corrected_files if 'npm install still failed' in f.get('changes', '') or 'mvn clean install still failed' in f.get('changes', '')]
        
        print(f"[LocalRepo]   - LLM successful corrections: {len(successful_corrections)}")
        if failed_corrections:
            print(f"[LocalRepo]   - LLM failed corrections: {len(failed_corrections)} (install still failed after multiple attempts)")
        print(f"[LocalRepo]   - React build status: {react_build_status}")
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
    print(f"[Step3] 🌐 Web search status: {'ENABLED' if OPENAI_CLIENT else 'DISABLED (OpenAI not available)'}")
    print(f"[Step3] 📊 Files processed: {len(regenerated_files)}")
    
    return regenerated_files

def convert_commonjs_to_es_modules(content: str) -> str:
    """Convert CommonJS syntax to ES modules for .js to .jsx conversion"""
    try:
        # Remove strict mode
        content = content.replace('"use strict";', '')
        content = content.replace("'use strict';", '')
        
        # Convert require statements to import statements
        content = re.sub(r'var\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*require\(["\']([^"\']+)["\']\);?', 
                        r'import \1 from "\2";', content)
        content = re.sub(r'const\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*require\(["\']([^"\']+)["\']\);?', 
                        r'import \1 from "\2";', content)
        
        # Convert destructured requires
        content = re.sub(r'const\s+\{([^}]+)\}\s*=\s*require\(["\']([^"\']+)["\']\);?', 
                        r'import { \1 } from "\2";', content)
        content = re.sub(r'var\s+\{([^}]+)\}\s*=\s*require\(["\']([^"\']+)["\']\);?', 
                        r'import { \1 } from "\2";', content)
        
        # Convert module.exports to export default
        content = re.sub(r'module\.exports\s*=\s*([a-zA-Z_][a-zA-Z0-9_]*);?', 
                       r'export default \1;', content)
        content = re.sub(r'module\.exports\s*=\s*\{([^}]+)\};?', 
                       r'export default {\1};', content)
        
        # Clean up CommonJS artifacts
        content = re.sub(r'exports\.__esModule\s*=\s*true;?', '', content)
        content = re.sub(r'Object\.defineProperty\(exports,\s*"__esModule",\s*\{\s*value:\s*true\s*\}\);?', '', content)
        
        # Clean up extra whitespace
        content = re.sub(r'\n\s*\n', '\n\n', content)
        content = content.strip()
        
        return content
    except Exception as e:
        print(f"[LocalRepo] ⚠️ Error converting CommonJS to ES modules: {e}")
        return content  # Return original if conversion fails

def run_mvn_install_with_error_correction(pom_dir_path, pom_file, repo_path, regenerated_files, pr_info):
    """
    Run mvn clean install with intelligent error correction using LLM.
    Keeps trying until success or max retries reached.
    Returns True if successful, False otherwise.
    """
    print(f"[LocalRepo] 📦 Starting Maven install with intelligent error correction...")
    
    MAX_INSTALL_CORRECTION_ATTEMPTS = 10  # Increased to handle both dependency and compilation errors
    
    def attempt_mvn_install():
        """Helper function to attempt mvn clean install"""
        try:
            result = subprocess.run(
                ["mvn", "clean", "install", "-DskipTests"],
                cwd=pom_dir_path,
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout for install
            )
            return result
        except subprocess.TimeoutExpired:
            print(f"[LocalRepo] ❌ mvn clean install timed out after 10 minutes")
            return None
        except FileNotFoundError:
            print("[LocalRepo] ❌ Maven not found. Please ensure Maven is installed and in PATH.")
            return None
        except Exception as e:
            print(f"[LocalRepo] ❌ Error during mvn clean install: {e}")
            return None
    
    # Initial attempt
    print(f"[LocalRepo] 📦 Attempting mvn clean install in {pom_dir_path}")
    result = attempt_mvn_install()
    
    if result is None:
        return False
    
    if result.returncode == 0:
        print(f"[LocalRepo] ✅ mvn clean install completed successfully")
        return True
    
    # Start the correction loop
    print(f"[LocalRepo] 🔄 Starting LLM error correction loop (max {MAX_INSTALL_CORRECTION_ATTEMPTS} attempts)...")
    
    correction_history = []
    
    for attempt in range(1, MAX_INSTALL_CORRECTION_ATTEMPTS + 1):
        print(f"[LocalRepo] 🤖 LLM Correction Attempt {attempt}/{MAX_INSTALL_CORRECTION_ATTEMPTS}")
        
        # Check if result is valid before accessing attributes
        if result is None:
            print(f"[LocalRepo] ❌ mvn clean install attempt returned None (timeout/error)")
            break
            
        print(f"[LocalRepo] ❌ mvn clean install failed with return code {result.returncode}")
        print(f"[LocalRepo] 📄 Error output:")
        print(f"[LocalRepo] stdout: {result.stdout}")
        print(f"[LocalRepo] stderr: {result.stderr}")
        
        # Combine error output for LLM analysis
        full_error = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        
        # Enhanced prompt with attempt context
        correction_context = ""
        if correction_history:
            correction_context = f"\n\nPREVIOUS CORRECTIONS APPLIED:\n" + "\n".join([f"Attempt {i+1}: {corr}" for i, corr in enumerate(correction_history)])
        
        print(f"[LocalRepo] 🤖 Extracting affected Java files from Maven error...")
        
        # Extract affected files using LLM
        try:
            affected_files = asyncio.run(
                extract_affected_java_files_from_error_with_llm(
                    full_error + correction_context, 
                    pom_dir_path, 
                    pr_info
                )
            )
        except Exception as e:
            print(f"[LocalRepo] ❌ Error extracting affected files: {e}")
            continue  # Try next iteration
        
        if not affected_files:
            print(f"[LocalRepo] ❌ Could not identify affected Java files for attempt {attempt}")
            continue  # Try next iteration
        
        print(f"[LocalRepo] 🤖 Fixing {len(affected_files)} Java files with LLM...")
        
        # Fix the affected files using LLM
        try:
            corrected_files = asyncio.run(
                fix_java_build_errors_with_llm(
                    full_error + correction_context,
                    affected_files,
                    pom_dir_path,
                    pr_info
                )
            )
        except Exception as e:
            print(f"[LocalRepo] ❌ Error running LLM Java correction: {e}")
            continue  # Try next iteration
        
        if not corrected_files:
            print(f"[LocalRepo] ❌ LLM could not provide valid Java corrections for attempt {attempt}")
            continue  # Try next iteration
        
        # Apply corrections to files
        files_changed = 0
        for file_path, corrected_content in corrected_files.items():
            full_file_path = os.path.join(pom_dir_path, file_path)
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
                pom_dir = os.path.dirname(pom_file) if os.path.dirname(pom_file) else "."
                relative_file_path = os.path.join(pom_dir, file_path) if pom_dir != "." else file_path
                regenerated_files[relative_file_path] = {
                    "old_code": current_content,
                    "changes": f"LLM-corrected Java compilation errors (attempt {attempt})",
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
        correction_history.append(f"Fixed {files_changed} Java files to resolve compilation errors")
        
        # Retry mvn clean install with corrected files
        print(f"[LocalRepo] 🔄 Retrying mvn clean install with LLM corrections {attempt}...")
        result = attempt_mvn_install()
        
        if result is None:
            continue  # Try next iteration
        
        if result.returncode == 0:
            print(f"[LocalRepo] 🎉 mvn clean install succeeded after {attempt} LLM correction(s)!")
            return True
        
        # If we get here, this correction didn't work, continue to next attempt
        print(f"[LocalRepo] ⚠️ mvn clean install still failed after correction {attempt}, trying next iteration...")
    
    # All correction attempts exhausted
    print(f"[LocalRepo] ❌ mvn clean install failed after {MAX_INSTALL_CORRECTION_ATTEMPTS} LLM correction attempts")
    if result is not None:
        print(f"[LocalRepo] 📄 Final error output:")
        print(f"[LocalRepo] stdout: {result.stdout}")
        print(f"[LocalRepo] stderr: {result.stderr}")
    else:
        print(f"[LocalRepo] 📄 Final attempt resulted in timeout/error (no output available)")
    
    return False

async def extract_affected_java_files_from_error_with_llm(build_error, pom_dir_path, pr_info):
    """
    Use LLM to intelligently extract affected Java files from Maven build errors.
    """
    print(f"[LocalRepo] 🧠 Using INTELLIGENT LLM file identification for Maven build errors")
    
    # Get list of all Java files in the project for reference
    all_files = []
    try:
        for root, dirs, files in os.walk(pom_dir_path):
            for file in files:
                if file.endswith(('.java', '.kt', '.xml')):
                    rel_path = os.path.relpath(os.path.join(root, file), pom_dir_path)
                    all_files.append(rel_path)
    except Exception as e:
        print(f"[LocalRepo] ⚠️ Error scanning directory: {e}")
    
    file_identification_prompt = f"""You are analyzing a Maven build error to identify which specific Java files need to be fixed. 

BUILD ERROR:
{build_error}

Available Java files in the project:
{chr(10).join(all_files[:50])}  {"... (truncated)" if len(all_files) > 50 else ""}

TASK: Identify the specific Java files that are causing this Maven build error and need to be modified to fix it.

IMPORTANT GUIDELINES:
1. Look for Java file paths mentioned in the error
2. Consider compilation errors, missing imports, or syntax issues
3. If there are dependency issues, identify the files that import the problematic dependencies
4. Be specific - return only files that actually exist and need modification
5. Focus on .java and .kt files

Return ONLY a JSON array of file paths, relative to the project root:
["src/main/java/com/example/App.java", "src/test/java/com/example/Test.java"]

Do not include any explanation, just the JSON array."""

    try:
        if not OPENAI_CLIENT:
            print(f"[LocalRepo] ⚠️ OpenAI not available for file identification")
            return []
        # Use OpenAI to identify files
        response = await asyncio.to_thread(
            OPENAI_CLIENT.chat.completions.create,
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": file_identification_prompt}],
            temperature=0.1
        )
        
        response_text = response.choices[0].message.content
        if response_text:
            response_text = response_text.strip()
        else:
            response_text = ""
        print(f"[LocalRepo] 🤖 LLM response: {response_text}")
        
        # Parse the JSON response
        # Extract JSON from markdown code blocks or use raw response
        json_content = None
        
        json_block_match = re.search(r'```json\s*\n?(.*?)```', response_text, re.DOTALL)
        if json_block_match:
            json_content = json_block_match.group(1).strip()
        elif '```' in response_text:
            code_block_match = re.search(r'```\s*\n?(.*?)```', response_text, re.DOTALL)
            if code_block_match:
                potential_json = code_block_match.group(1).strip()
                if potential_json.startswith('[') and potential_json.endswith(']'):
                    json_content = potential_json
            
        if not json_content:
            json_content = response_text.strip()
        
        affected_files = json.loads(json_content)
        
        if isinstance(affected_files, list):
            # Filter to only files that actually exist
            existing_files = []
            for file_path in affected_files:
                full_path = os.path.join(pom_dir_path, file_path)
                if os.path.exists(full_path):
                    existing_files.append(file_path)
                else:
                    print(f"[LocalRepo] ⚠️ LLM suggested {file_path} but file doesn't exist")
            
            print(f"[LocalRepo] ✅ LLM identified {len(existing_files)} affected Java files: {existing_files}")
            return existing_files
        else:
            print(f"[LocalRepo] ❌ LLM response is not a valid array: {type(affected_files)}")
            return []
            
    except json.JSONDecodeError as e:
        print(f"[LocalRepo] ❌ Could not parse LLM response as JSON: {e}")
        return []
        
    except Exception as e:
        print(f"[LocalRepo] ❌ Error with LLM file identification: {e}")
        return []

async def fix_java_build_errors_with_llm(build_error, affected_files, pom_dir_path, pr_info):
    """
    Use LLM to fix Java compilation errors.
    Conservative approach - only small, necessary changes.
    """
    print(f"[LocalRepo] 🧠 Using LLM to fix Java build errors in {len(affected_files)} files")
    
    corrected_files = {}
    
    for file_path in affected_files:
        full_file_path = os.path.join(pom_dir_path, file_path)
        
        try:
            # Read the current file content
            with open(full_file_path, "r", encoding="utf-8") as f:
                current_content = f.read()
        except Exception as e:
            print(f"[LocalRepo] ❌ Error reading {file_path}: {e}")
            continue
        
        # Determine file type for appropriate prompt
        file_ext = file_path.split('.')[-1].lower()
        language = "Java" if file_ext == "java" else "Kotlin" if file_ext == "kt" else "Java"
        
        java_prompt = f"""You are a {language} expert fixing compilation errors.

BUILD ERROR:
{build_error}

CURRENT {language.upper()} FILE ({file_path}):
{current_content}

TASK: Fix the compilation errors in this {language} file.

IMPORTANT GUIDELINES:
1. Make ONLY small, necessary changes to fix the specific compilation error
2. Do NOT add new features or methods unless explicitly required by the error
3. Focus on syntax errors, missing imports, or type mismatches
4. Preserve the existing code structure and logic
5. Be conservative - if unsure, make minimal changes
6. Only fix the specific error mentioned in the build output

Return ONLY the corrected {language} code. Do not include any explanation or markdown formatting."""

        try:
            if not OPENAI_CLIENT:
                print(f"[LocalRepo] ⚠️ OpenAI not available for {language} correction")
                continue
            
            # Use OpenAI to fix Java/Kotlin file
            response = await asyncio.to_thread(
                OPENAI_CLIENT.chat.completions.create,
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": java_prompt}],
                temperature=0.1
            )
            
            corrected_content = response.choices[0].message.content
            if corrected_content:
                corrected_content = corrected_content.strip()
                
                # Clean up the response - remove markdown code blocks if present
                if corrected_content.startswith(f'```{file_ext}'):
                    corrected_content = corrected_content[len(f'```{file_ext}'):]
                elif corrected_content.startswith('```'):
                    corrected_content = corrected_content[3:]
                if corrected_content.endswith('```'):
                    corrected_content = corrected_content[:-3]
                corrected_content = corrected_content.strip()
                
                # Only include if content actually changed
                if corrected_content != current_content:
                    corrected_files[file_path] = corrected_content
                    print(f"[LocalRepo] ✅ LLM provided {language} correction for {file_path}")
                else:
                    print(f"[LocalRepo] ⚠️ LLM returned same content for {file_path} (no changes)")
            else:
                print(f"[LocalRepo] ⚠️ LLM returned empty response for {file_path}")
                
        except Exception as e:
            print(f"[LocalRepo] ❌ Error with LLM {language} correction for {file_path}: {e}")
            continue
    
    print(f"[LocalRepo] ✅ LLM provided corrections for {len(corrected_files)} {language} files")
    return corrected_files