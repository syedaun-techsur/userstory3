"""
Step 3: AI Code Regeneration with Real-Time Web Search

This module implements intelligent code regeneration using:
1. 🌐 REAL-TIME WEB SEARCH for latest practices and patterns
2. 🔄 Dynamic context caching for progressive refinement
3. 🛠️ Intelligent error correction with web search feedback loops
4. 📦 Dependency optimization and build validation

Key Features:
- Web search for current best practices, security patterns, and API changes
- Automatic fallback from web search to MCP when OpenAI is unavailable
- Progressive context building where later files see refined versions of earlier files
- Intelligent package.json dependency analysis with real-time version checking
- Build error correction with web search for latest solutions
- Local repository processing with npm install and build validation

Web Search Integration:
- Primary code generation uses OpenAI with web search when available
- All error correction flows use web search to find current solutions
- Package.json files get comprehensive dependency analysis via web search
- Build errors are resolved using latest documentation and patterns
"""

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

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not GITHUB_TOKEN:
    print("❌ GITHUB_TOKEN environment variable not set")
    exit(1)

# Initialize direct GitHub client
github_direct = Github(GITHUB_TOKEN)
print(f"[DEBUG] ✅ GitHub API client initialized")

MAX_CONTEXT_CHARS = 4000000  # GPT-4.1 Mini has 1M+ token context window (1M tokens ≈ 4M chars)

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
    Fetch context using dynamic cache with updated file contents.
    Uses refined versions of previously processed files.
    """
    context = ""
    total_chars = 0
    refined_files_count = 0
    original_files_count = 0
    
    if processed_files is None:
        processed_files = set()
    
    print(f"[Step3] 🔄 Building dynamic context for {target_file}...")
    
    for file_name in pr_files:
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
            
            section = f"\n// File: {file_name} ({file_size} chars) [{status}]\n{file_content}\n"
            
            # Keep reasonable limit to avoid overwhelming the model
            if total_chars + len(section) > MAX_CONTEXT_CHARS:
                print(f"[Step3] ⚠️ Context size limit reached ({MAX_CONTEXT_CHARS} chars), stopping context build")
                break
                
            context += section
            total_chars += len(section)
            
            if is_refined:
                refined_files_count += 1
                print(f"[Step3] ✅ Added {file_name} to context ({file_size} chars) - REFINED VERSION")
            else:
                original_files_count += 1
                print(f"[Step3] 📄 Added {file_name} to context ({file_size} chars) - ORIGINAL VERSION")
        else:
            print(f"[Step3] ⚠️ Warning: {file_name} not found in dynamic cache")
    
    print(f"[Step3] 📊 Dynamic context summary for {target_file}:")
    print(f"[Step3]   - Total chars: {total_chars:,}")
    print(f"[Step3]   - Refined files in context: {refined_files_count}")
    print(f"[Step3]   - Original files in context: {original_files_count}")
    print(f"[Step3]   - Total context files: {refined_files_count + original_files_count}")
    
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
        f"You are an expert AI code reviewer. Your job is to improve and refactor ONLY the given file `{file_name}` "
        f"so that it meets the following coding standards:\n\n"
        f"{requirements}\n\n"
        f"---\n Repository Context (other files for reference):\n{context}\n"
        f"---\n Current Code ({file_name} - {file_extension} file):\n```{file_extension}\n{code}\n```\n"
    )
    
    if is_package_json:
        dependency_instructions = (
            f"\n---\n🔍 SPECIAL PACKAGE.JSON DEPENDENCY ANALYSIS:\n"
            f"This is a package.json file. You MUST perform strict dependency analysis:\n\n"
            f"1. CAREFULLY ANALYZE the context files above to identify ALL imports and dependencies actually used\n"
            f"2. SCAN for: import statements, require(), @types/ packages, testing libraries, build tools\n"
            f"3. REMOVE any unused dependencies that are not imported in the context files\n"
            f"4. ADD any missing dependencies that are imported but not listed\n"
            f"5. UPDATE dependency versions to latest stable versions (check compatibility)\n"
            f"6. ORGANIZE dependencies into correct sections (dependencies vs devDependencies)\n"
            f"7. ENSURE TypeScript types are in devDependencies (@types/*, typescript, etc.)\n"
            f"8. ENSURE testing frameworks are in devDependencies (jest, @testing-library/*, etc.)\n"
            f"9. ENSURE build tools are in devDependencies (webpack, babel, eslint, etc.)\n"
            f"10. VERIFY peer dependencies are properly handled\n\n"
            f"🚨 CRITICAL: Only include dependencies that are ACTUALLY USED in the context files above.\n"
            f"Do NOT add speculative or 'might need' dependencies. Be STRICT and PRECISE.\n\n"
            f"📦 DEPENDENCY PHILOSOPHY: More dependencies = More problems\n"
            f"- Be CONSERVATIVE with adding new dependencies\n"
            f"- Only add dependencies when absolutely necessary\n"
            f"- Prefer built-in solutions over external packages when possible\n"
            f"- Use well-maintained, popular packages over niche alternatives\n"
            f"- Avoid adding dependencies for features that might be used later\n"
        )
    else:
        dependency_instructions = (
            f"\n---\n📦 DEPENDENCY & BUILD ERROR PREVENTION:\n"
            f"For import statements and dependencies:\n"
            f"1. ONLY use dependencies that exist in package.json or are built-in\n"
            f"2. Do NOT add new import statements for packages not already available\n"
            f"3. If you need a new dependency, mention it in the ### Changes section\n"
            f"4. Prefer using existing dependencies from the context over adding new ones\n\n"
            f"🚫 PREVENT COMMON BUILD ERRORS:\n"
            f"1. **Unused Imports**: Remove ALL unused imports (like unused React hooks)\n"
            f"   - If you import useEffect but don't use it, REMOVE the import\n"
            f"   - Clean up any imported functions, components, or types that aren't used\n"
            f"2. **Missing Dependencies**: Ensure all imported modules are available\n"
            f"   - Check that every import statement has a corresponding dependency\n"
            f"   - For React Router, ensure 'react-router-dom' is in dependencies\n"
            f"   - For UI libraries, ensure they're properly listed\n"
            f"3. **TypeScript Issues**: Fix TypeScript configuration problems\n"
            f"   - Ensure @types/* packages are available for all external libraries\n"
            f"   - Remove or fix invalid type references\n"
            f"   - Use proper TypeScript syntax and type annotations\n"
            f"4. **File Path Issues**: Use correct relative/absolute paths\n"
            f"   - Verify all import paths are correct and files exist\n"
            f"   - Use proper case sensitivity for file names\n\n"
            f"📦 DEPENDENCY PHILOSOPHY: More dependencies = More problems\n"
            f"- Be EXTREMELY CONSERVATIVE with suggesting new dependencies\n"
            f"- Only suggest adding dependencies when absolutely necessary\n"
            f"- Try to solve problems with existing dependencies first\n"
            f"- If you must add a dependency, explain why it's essential\n"
        )
    
    format_instructions = (
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
    
    return base_prompt + dependency_instructions + format_instructions

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
    """Extract changes section from AI response and clean up URLs/citations"""
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
    
    # Clean up URLs and citations from web search
    if changes:
        print(f"[Step3] 🧹 Cleaning up URLs and citations from changes for {file_name}...")
        
        # Remove URLs in parentheses with citations
        # Pattern: ([domain.com](url)) or ([description](url))
        changes = re.sub(r'\s*\(\[[^\]]+\]\([^)]+\)\)', '', changes)
        
        # Remove standalone URLs in parentheses
        # Pattern: (https://example.com/...)
        changes = re.sub(r'\s*\(https?://[^)]+\)', '', changes)
        
        # Remove bare URLs
        changes = re.sub(r'https?://[^\s)]+', '', changes)
        
        # Remove citation patterns like ([source.com](url))
        changes = re.sub(r'\s*\([^)]*\.com[^)]*\)', '', changes)
        
        # Remove utm_source parameters that might remain
        changes = re.sub(r'[?&]utm_source=[^)\s]*', '', changes)
        
        # Clean up any double spaces or trailing periods from URL removal
        changes = re.sub(r'\s{2,}', ' ', changes)  # Multiple spaces to single space
        changes = re.sub(r'\s*\.\s*\n', '.\n', changes)  # Clean up trailing periods
        changes = re.sub(r'\n\s*\n', '\n', changes)  # Clean up extra newlines
        
        # Clean up any remaining markdown artifacts
        changes = re.sub(r'\[\]', '', changes)  # Empty markdown links
        changes = re.sub(r'\(\)', '', changes)  # Empty parentheses
        
        changes = changes.strip()
        
        print(f"[Step3] ✅ Cleaned changes section for {file_name}")
    
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

async def process_single_file(session, file_name: str, old_code: str, requirements: str, pr_info: Optional[dict] = None, dynamic_context_cache: Optional[Dict[str, str]] = None, pr_files: Optional[Set[str]] = None, processed_files: Optional[Set[str]] = None) -> dict:
    """Process a single file through the AI refinement pipeline with dynamic context"""
    try:
        print(f"[Step3] Processing file: {file_name}")
        
        # Fetch context using dynamic cache if available, otherwise fall back to static context
        if dynamic_context_cache is not None and pr_files is not None:
            print(f"[Step3] Using dynamic context for {file_name}")
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

async def process_single_file_with_web_search(file_name: str, old_code: str, requirements: str, pr_info: Optional[dict] = None, dynamic_context_cache: Optional[Dict[str, str]] = None, pr_files: Optional[Set[str]] = None, processed_files: Optional[Set[str]] = None) -> dict:
    """Process a single file through AI refinement pipeline using OpenAI with web search for latest practices"""
    try:
        print(f"[Step3] 🌐 Processing file with WEB SEARCH: {file_name}")
        
        if not OPENAI_CLIENT:
            print(f"[Step3] ⚠️ OpenAI client not available - falling back to regular MCP")
            # We need to use MCP with a session, so this function should only be called when OpenAI is available
            # For fallback, the main function should call process_single_file directly
            raise Exception("OpenAI client not available - use regular MCP process_single_file instead")
        
        # Fetch context using dynamic cache if available
        if dynamic_context_cache is not None and pr_files is not None:
            print(f"[Step3] Using dynamic context for {file_name}")
            context = fetch_dynamic_context(file_name, dynamic_context_cache, pr_files, processed_files)
        else:
            print(f"[Step3] Falling back to static context for {file_name}")
            if pr_info is None:
                raise ValueError("pr_info cannot be None")
            repo_name = pr_info["repo_name"]
            pr_number = pr_info["pr_number"]
            context = fetch_repo_context(repo_name, pr_number, file_name, pr_info)
        
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
    """Create a web search enhanced prompt for code generation with latest practices"""
    # Get the file extension for the AI to understand the language
    file_extension = file_name.split('.')[-1].lower()
    
    # Check if this is a package.json file for special dependency handling
    is_package_json = file_name.endswith('package.json')
    
    base_prompt = (
        f"You are an expert AI code reviewer with access to real-time web search. Your job is to improve and refactor ONLY the given file `{file_name}` "
        f"using the latest coding standards, best practices, and current technology trends.\n\n"
        f"REQUIREMENTS FROM PROJECT:\n{requirements}\n\n"
        f"---\nRepository Context (other files for reference):\n{context}\n"
        f"---\nCurrent Code ({file_name} - {file_extension} file):\n```{file_extension}\n{code}\n```\n"
    )
    
    web_search_instructions = (
        f"\n---\n🌐 **WEB SEARCH ENHANCEMENT INSTRUCTIONS:**\n"
        f"You have access to real-time web search. Use it to ensure your code follows the LATEST practices:\n\n"
        f"1. **SEARCH for current best practices** for the specific technology/framework used in this file\n"
        f"2. **VERIFY latest syntax** and API changes for the libraries/frameworks involved\n"
        f"3. **CHECK for security vulnerabilities** and modern security practices\n"
        f"4. **FIND performance optimization** techniques for the specific technology\n"
        f"5. **DISCOVER breaking changes** in recent versions of dependencies\n"
        f"6. **LOOK UP accessibility (a11y)** standards and modern requirements\n"
        f"7. **RESEARCH testing patterns** and modern testing approaches\n"
        f"8. **VERIFY TypeScript best practices** if applicable\n"
        f"9. **CHECK modern React patterns** if it's a React component\n"
        f"10. **FIND current ESLint/Prettier** configuration standards\n\n"
        f"🔍 **SEARCH STRATEGY:**\n"
        f"- Search for '{file_extension} best practices 2024'\n"
        f"- Search for specific library/framework + 'latest version changes'\n"
        f"- Search for 'modern {file_extension} patterns'\n"
        f"- Search for security and performance optimizations\n"
        f"- Verify any imports/dependencies are using latest stable versions\n\n"
        f"🎯 **ALWAYS PRIORITIZE:**\n"
        f"- **CURRENT/LATEST** information over outdated practices\n"
        f"- **SECURITY** - implement latest security best practices\n"
        f"- **PERFORMANCE** - use modern optimization techniques\n"
        f"- **ACCESSIBILITY** - follow current a11y standards\n"
        f"- **MAINTAINABILITY** - use patterns that are currently recommended\n"
        f"- **TYPE SAFETY** - implement strong typing where applicable\n"
    )
    
    if is_package_json:
        dependency_instructions = (
            f"\n---\n🔍 **PACKAGE.JSON WEB SEARCH ANALYSIS:**\n"
            f"This is a package.json file. Use web search to perform COMPREHENSIVE dependency analysis:\n\n"
            f"1. **SEARCH for each dependency** to verify:\n"
            f"   - Current stable version (not just latest - check for stability)\n"
            f"   - Breaking changes in recent versions\n"
            f"   - Security vulnerabilities\n"
            f"   - Deprecated packages (search for alternatives)\n"
            f"   - Peer dependency requirements\n\n"
            f"2. **VERIFY compatibility** between packages:\n"
            f"   - Search for known conflicts between major dependencies\n"
            f"   - Check React/Vue/Angular version compatibility\n"
            f"   - Verify TypeScript version compatibility\n\n"
            f"3. **DISCOVER modern alternatives** to outdated packages:\n"
            f"   - Search for 'alternatives to [package-name] 2024'\n"
            f"   - Look for more maintained/performant options\n"
            f"   - Check GitHub stars, maintenance activity\n\n"
            f"4. **ANALYZE the context files** to see what's actually imported and used\n"
            f"5. **REMOVE unused dependencies** that aren't imported anywhere\n"
            f"6. **ADD missing dependencies** that are imported but not listed\n"
            f"7. **UPDATE versions** to current stable releases\n"
            f"8. **ORGANIZE properly** (dependencies vs devDependencies)\n\n"
            f"🚨 **CRITICAL WEB SEARCH QUERIES:**\n"
            f"- '[package-name] latest stable version 2024'\n"
            f"- '[package-name] security vulnerabilities'\n"
            f"- '[package-name] breaking changes'\n"
            f"- 'alternatives to [package-name] 2024'\n"
            f"- 'React/TypeScript/Node.js version compatibility 2024'\n\n"
            f"💡 **PHILOSOPHY**: Use web search to make INFORMED decisions about dependencies\n"
            f"- Only include packages that are ACTIVELY MAINTAINED\n"
            f"- Prefer packages with strong community support\n"
            f"- Choose stable versions over bleeding edge\n"
            f"- Security is paramount - search for any CVEs\n"
        )
    else:
        dependency_instructions = (
            f"\n---\n📦 **DEPENDENCY & IMPORT WEB SEARCH VERIFICATION:**\n"
            f"For import statements and dependencies:\n"
            f"1. **SEARCH for current import syntax** for each library/framework used\n"
            f"2. **VERIFY API changes** in recent versions of imported packages\n"
            f"3. **CHECK for deprecated imports** and find modern replacements\n"
            f"4. **DISCOVER new features** in current versions that could improve the code\n"
            f"5. **FIND breaking changes** that might affect imports\n\n"
            f"🚫 **BUILD ERROR PREVENTION WITH WEB SEARCH:**\n"
            f"1. **Search for common build errors** with the specific framework/library\n"
            f"2. **Verify TypeScript configuration** best practices for 2024\n"
            f"3. **Check for import/export issues** in current versions\n"
            f"4. **Search for linting rule updates** and modern ESLint configs\n"
            f"5. **Find accessibility patterns** for UI components\n\n"
            f"💡 **MODERN CODE PATTERNS:**\n"
            f"- Search for 'modern {file_extension} patterns 2024'\n"
            f"- Look up current React hooks best practices\n"
            f"- Find latest TypeScript utility types\n"
            f"- Check for new CSS-in-JS solutions\n"
            f"- Verify current testing patterns\n"
        )
    
    format_instructions = (
        f"\n---\nPlease return the updated code and changes in the following EXACT format:\n"
        f"### Changes:\n- A clean bullet-point summary of what was changed based on web search findings.\n\n"
        f"### Updated Code:\n```{file_extension}\n<ONLY THE NEW/IMPROVED CODE HERE>\n```\n\n"
        f"⚠️ **CRITICAL REQUIREMENTS:**\n"
        f"1. **USE WEB SEARCH** to verify all suggestions and find latest practices\n"
        f"2. **MENTION WEB SEARCH FINDINGS** in the changes section but keep it clean\n"
        f"3. **DO NOT include URLs, links, or citations** in the changes section\n"
        f"4. **Keep changes section professional** - no parenthetical references or links\n"
        f"5. Do NOT use <think> tags or any other XML-like tags\n"
        f"6. Provide bullet-point summary of changes under the `### Changes` heading\n"
        f"7. Provide ONLY ONE code block under the `### Updated Code` heading\n"
        f"8. Do NOT show the old code again in your response\n"
        f"9. Do NOT suggest creating new files. Only update this file\n"
        f"10. The response must start with `### Changes:` and end with the code block\n"
        f"11. Return ONLY the improved/refactored code using LATEST practices\n"
        f"12. Return the SAME TYPE of code as the original file ({file_extension})\n"
        f"13. If the code already meets current best practices:\n"
        f"    - In the ### Changes section, write: 'No changes needed - code follows current best practices.'\n"
        f"    - In the ### Updated Code section, return the original code unchanged.\n"
        f"14. **VERIFY with web search** before making any claims about best practices\n"
        f"15. **CHANGES FORMAT**: Use simple, clean bullet points like:\n"
        f"    - Enhanced accessibility with proper ARIA labels\n"
        f"    - Updated to latest React patterns and hooks\n"
        f"    - Improved error handling and validation\n"
        f"    - Added TypeScript strict typing\n"
    )
    
    return base_prompt + web_search_instructions + dependency_instructions + format_instructions

async def regenerate_code_with_mcp(files: Dict[str, str], requirements: str, pr, pr_info=None) -> Dict[str, Dict[str, str]]:
    """Main function to regenerate code using MCP with dynamic context updates"""
    regenerated = {}
    server_params = StdioServerParameters(command="python", args=["server.py"])

    # Accumulate total token usage
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    # Initialize dynamic context cache
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
    
    print(f"[Step3] 📦 Processing order optimized for dependencies:")
    print(f"[Step3]   - Regular files first: {len(regular_files)} files")
    print(f"[Step3]   - Package.json files last: {len(package_json_files)} files")
    print(f"[Step3]   - This ensures package.json sees all refined dependencies")

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
                        print(f"[Step3] 🔍 DEPENDENCY ANALYSIS MODE: AI will analyze all refined files for exact dependencies")
                    else:
                        print(f"[Step3] 🔄 Processing file {current_file_number}/{total_files}: {file_name}")
                    
                    print(f"[Step3] 📊 Context status: {len(processed_files)} files already refined, {total_files - current_file_number} files remaining")
                    
                    # Use web search for code generation if available, otherwise fallback to MCP
                    if OPENAI_CLIENT:
                        print(f"[Step3] 🌐 Using web search for code generation: {file_name}")
                        file_result = await process_single_file_with_web_search(
                            file_name, 
                            old_code, 
                            requirements, 
                            pr_info,
                            dynamic_context_cache,
                            pr_files,
                            processed_files
                        )
                    else:
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
                    
                    # Extract token usage and accumulate
                    prompt_tokens, completion_tokens, tokens = file_result.pop("token_usage", (0, 0, 0))
                    total_prompt_tokens += prompt_tokens
                    total_completion_tokens += completion_tokens
                    total_tokens += tokens
                    
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

        # Print final summary with web search and dynamic context benefits
        print(f"[Step3] 🎉 AI processing completed with WEB SEARCH, dynamic context and dependency optimization!")
        print(f"[Step3] 📊 Processing Summary:")
        print(f"[Step3]   - Total files processed: {len(regenerated)}")
        print(f"[Step3]   - Regular files refined: {len(regular_files)}")
        print(f"[Step3]   - Package.json files analyzed: {len(package_json_files)}")
        print(f"[Step3]   - Web search enabled: {'✅ YES' if OPENAI_CLIENT else '❌ NO (OpenAI client not available)'}")
        print(f"[Step3]   - Dynamic context benefit: Later files used refined versions of earlier files")
        print(f"[Step3]   - Dependency optimization: Package.json files processed last with full context")
        print(f"[Step3]   - Latest practices: {'✅ Using real-time web search' if OPENAI_CLIENT else '⚠️ Using static knowledge only'}")
        
        # Print final token usage and pricing
        print(f"[Step3] 💰 TOTAL TOKEN USAGE: prompt_tokens={total_prompt_tokens:,}, completion_tokens={total_completion_tokens:,}, total_tokens={total_tokens:,}")
        
        # Calculate and print total API price for OpenAI GPT-4.1 Mini
        input_price = (total_prompt_tokens / 1000) * 0.00042
        output_price = (total_completion_tokens / 1000) * 0.00168
        total_price = input_price + output_price
        print(f"[Step3] 💵 OpenAI GPT-4.1 Mini API PRICING: Total=${total_price:.4f} (input=${input_price:.4f}, output=${output_price:.4f})")

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

async def fix_package_json_with_web_search(package_json_content, npm_error, package_file_path, pr_info):
    """
    Use OpenAI with web search to fix package.json based on npm install errors.
    This function uses web search to find current package versions and fix version conflicts.
    Returns corrected package.json content or None if correction fails.
    """
    if not OPENAI_CLIENT:
        print("[LocalRepo] ⚠️ OpenAI client not available - falling back to regular LLM")
        return await fix_package_json_with_llm(package_json_content, npm_error, package_file_path, pr_info)
    
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
            return await fix_package_json_with_llm(package_json_content, npm_error, package_file_path, pr_info)
            
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during web search package.json correction: {e}")
        print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
        return await fix_package_json_with_llm(package_json_content, npm_error, package_file_path, pr_info)

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

async def fix_package_json_for_build_errors_with_web_search(package_json_content, build_error, package_dir_path, pr_info):
    """
    Use OpenAI with web search to fix package.json based on build errors that indicate missing dependencies.
    This function uses web search to find current package versions and resolve dependency issues.
    Returns corrected package.json content or None if correction fails.
    """
    if not OPENAI_CLIENT:
        print("[LocalRepo] ⚠️ OpenAI client not available - falling back to regular LLM")
        return await fix_package_json_for_build_errors(package_json_content, build_error, package_dir_path, pr_info)
    
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
            return await fix_package_json_for_build_errors(package_json_content, build_error, package_dir_path, pr_info)
            
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during web search package.json build dependency correction: {e}")
        print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
        return await fix_package_json_for_build_errors(package_json_content, build_error, package_dir_path, pr_info)

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

IMPORTANT: 
- Only include files that actually need changes
- Return the complete corrected file content for each file
- Do NOT suggest adding new dependencies unless absolutely essential
- Focus on removing unused code rather than adding new code"""

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
                    return None
                    
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during LLM build error correction: {e}")
        return None

async def fix_build_errors_with_web_search(build_error, affected_files, package_dir_path, pr_info):
    """
    Use OpenAI with web search to fix TypeScript/build configuration errors.
    This function specifically targets build configuration issues that benefit from web search.
    Returns dict of corrected files or None if correction fails.
    """
    if not OPENAI_CLIENT:
        print("[LocalRepo] ⚠️ OpenAI client not available - falling back to regular LLM")
        return await fix_build_errors_with_llm(build_error, affected_files, package_dir_path, pr_info)
    
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
            return await fix_build_errors_with_llm(build_error, affected_files, package_dir_path, pr_info)
            
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during web search build error correction: {e}")
        print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
        return await fix_build_errors_with_llm(build_error, affected_files, package_dir_path, pr_info)

def run_npm_install_with_error_correction(package_dir_path, package_file, repo_path, regenerated_files, pr_info):
    """
    Run npm install with intelligent error correction using LLM in a loop.
    Keeps trying until success or max retries reached.
    Returns True if successful, False otherwise.
    """
    package_dir = os.path.dirname(package_file) if os.path.dirname(package_file) else "."
    MAX_CORRECTION_ATTEMPTS = 5  # Maximum number of LLM correction attempts
    
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
    MAX_BUILD_CORRECTION_ATTEMPTS = 8  # Maximum number of LLM correction attempts per package
    
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

    def extract_affected_files_from_error(build_error, package_dir_path):
        """Extract file paths mentioned in build errors"""
        affected_files = set()
        
        # Common patterns for file paths in TypeScript/JavaScript build errors
        file_patterns = [
            r'([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))\(\d+,\d+\):',  # TypeScript errors: file.ts(1,1): error
            r'in ([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))',           # Webpack/build errors: in src/file.ts
            r'([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json)):\d+:\d+',     # ESLint style: file.ts:1:1
            r"'([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))'",           # Quoted file references
        ]
        
        for pattern in file_patterns:
            matches = re.findall(pattern, build_error)
            for match in matches:
                # Convert to relative path from package directory
                if match.startswith('./') or match.startswith('../'):
                    affected_files.add(match)
                elif match.startswith('/'):
                    # Absolute path - try to make it relative to package dir
                    try:
                        rel_path = os.path.relpath(match, package_dir_path)
                        if not rel_path.startswith('..'):  # Only if it's within the package dir
                            affected_files.add(rel_path)
                    except:
                        pass
                else:
                    # Assume it's relative to package root
                    affected_files.add(match)
        
        # Filter to only files that actually exist
        existing_files = []
        for file_path in affected_files:
            full_path = os.path.join(package_dir_path, file_path)
            if os.path.exists(full_path):
                existing_files.append(file_path)
        
        return existing_files
    
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
                            fix_package_json_for_build_errors_with_web_search(
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
            affected_files = extract_affected_files_from_error(full_build_error, package_dir_path)
            
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
                    fix_build_errors_with_web_search(
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
        
        # Count LLM corrections with more detail
        llm_corrected_files = [f for f in regenerated_files.values() if 'LLM-corrected' in f.get('changes', '') or 'LLM attempted' in f.get('changes', '')]
        successful_corrections = [f for f in llm_corrected_files if 'npm install still failed' not in f.get('changes', '')]
        failed_corrections = [f for f in llm_corrected_files if 'npm install still failed' in f.get('changes', '')]
        
        print(f"[LocalRepo]   - LLM successful corrections: {len(successful_corrections)}")
        if failed_corrections:
            print(f"[LocalRepo]   - LLM failed corrections: {len(failed_corrections)} (npm install still failed after multiple attempts)")
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
    print(f"[Step3] 🌐 Web search status: {'ENABLED' if OPENAI_CLIENT else 'DISABLED (OpenAI not available)'}")
    print(f"[Step3] 📊 Files processed: {len(regenerated_files)}")
    
    return regenerated_files