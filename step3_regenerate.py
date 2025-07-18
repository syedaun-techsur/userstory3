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

def extract_external_dependencies(file_path: str, file_content: str) -> Set[str]:
    """
    Extract external dependencies (npm packages) from a file's import statements.
    Returns set of package names (not local file paths).
    """
    external_deps = set()
    file_dir = os.path.dirname(file_path)
    file_ext = file_path.split('.')[-1].lower()
    
    try:
        if file_ext in ['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs']:
            external_deps.update(extract_js_ts_external_dependencies(file_content))
        elif file_ext in ['json']:
            external_deps.update(extract_json_external_dependencies(file_content, file_path))
        # Add more languages as needed
        
    except Exception as e:
        print(f"[Step3] ⚠️ Error extracting external dependencies from {file_path}: {e}")
    
    return external_deps

def extract_js_ts_external_dependencies(content: str) -> Set[str]:
    """Extract external npm packages from JavaScript/TypeScript imports"""
    external_deps = set()
    
    # Pattern to match import statements
    import_patterns = [
        # ES6 imports: import React from 'react'
        r'import\s+.*?\s+from\s+[\'"`]([^\'"`]+)[\'"`]',
        # Direct imports: import 'react'
        r'import\s+[\'"`]([^\'"`]+)[\'"`]',
        # Dynamic imports: import('react')
        r'import\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\s*\)',
        # CommonJS require: require('react')
        r'require\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\s*\)',
        # TypeScript imports: import type { } from 'react'
        r'import\s+type\s+.*?\s+from\s+[\'"`]([^\'"`]+)[\'"`]',
        # Re-exports: export * from 'react'
        r'export\s+.*?\s+from\s+[\'"`]([^\'"`]+)[\'"`]',
    ]
    
    for pattern in import_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            # Skip local/relative imports (start with ./ or ../)
            if match.startswith(('./', '../', '/')):
                continue
            
            # Extract package name (handle scoped packages like @types/node)
            if match.startswith('@'):
                # Scoped package: @types/node or @babel/core
                parts = match.split('/')
                if len(parts) >= 2:
                    package_name = f"{parts[0]}/{parts[1]}"
                else:
                    package_name = parts[0]
            else:
                # Regular package: react, lodash, etc.
                package_name = match.split('/')[0]
            
            external_deps.add(package_name)
    
    return external_deps



def extract_json_external_dependencies(content: str, file_path: str) -> Set[str]:
    """Extract dependencies from JSON files like package.json"""
    external_deps = set()
    
    try:
        data = json.loads(content)
        
        # For package.json files, extract dependencies
        if file_path.endswith('package.json'):
            for dep_type in ['dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies']:
                if dep_type in data:
                    external_deps.update(data[dep_type].keys())
        
        # For tsconfig.json, extract @types packages
        elif file_path.endswith('tsconfig.json'):
            if 'compilerOptions' in data and 'types' in data['compilerOptions']:
                for type_pkg in data['compilerOptions']['types']:
                    if not type_pkg.startswith('@types/'):
                        external_deps.add(f'@types/{type_pkg}')
                    else:
                        external_deps.add(type_pkg)
    
    except json.JSONDecodeError:
        pass
    
    return external_deps

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

def parse_file_dependencies(file_path: str, file_content: str, pr_files: Set[str]) -> Set[str]:
    """
    Parse a file's imports/dependencies and return the set of PR files it depends on.
    
    Args:
        file_path: Path of the target file
        file_content: Content of the target file
        pr_files: Set of all files in the PR
    
    Returns:
        Set of file paths from pr_files that this file depends on
    """
    dependencies = set()
    file_dir = os.path.dirname(file_path)
    
    # Get file extension to determine parsing strategy
    file_ext = file_path.split('.')[-1].lower()
    
    print(f"[Step3] 🔍 Parsing dependencies for {file_path} (type: {file_ext})")
    
    try:
        if file_ext in ['js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs']:
            # JavaScript/TypeScript files
            dependencies.update(parse_js_ts_dependencies(file_content, file_dir, pr_files))
        
        elif file_ext in ['py']:
            # Python files
            dependencies.update(parse_python_dependencies(file_content, file_dir, pr_files))
        
        elif file_ext in ['css', 'scss', 'sass', 'less']:
            # CSS files (imports other CSS files)
            dependencies.update(parse_css_dependencies(file_content, file_dir, pr_files))
        
        elif file_ext in ['json'] and file_path.endswith('package.json'):
            # Special case: package.json files don't have local dependencies
            print(f"[Step3] 📦 package.json detected - no local dependencies to parse")
            return set()  # Empty set - handled separately with dependency summary
        
        elif file_ext in ['html', 'htm']:
            # HTML files (script tags, link tags)
            dependencies.update(parse_html_dependencies(file_content, file_dir, pr_files))
        
        else:
            # Unknown file type - check for common patterns
            dependencies.update(parse_generic_dependencies(file_content, file_dir, pr_files))
    
    except Exception as e:
        print(f"[Step3] ⚠️ Error parsing dependencies for {file_path}: {e}")
    
    print(f"[Step3] 🔗 Found {len(dependencies)} dependencies for {file_path}: {list(dependencies)}")
    return dependencies

def parse_js_ts_dependencies(content: str, file_dir: str, pr_files: Set[str]) -> Set[str]:
    """Parse JavaScript/TypeScript import statements"""
    dependencies = set()
    
    # Common import patterns
    import_patterns = [
        # ES6 imports
        r'import\s+.*?\s+from\s+[\'"`]([^\'"`]+)[\'"`]',
        r'import\s+[\'"`]([^\'"`]+)[\'"`]',
        # Dynamic imports
        r'import\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\s*\)',
        # CommonJS require
        r'require\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\s*\)',
        # TypeScript specific
        r'import\s+type\s+.*?\s+from\s+[\'"`]([^\'"`]+)[\'"`]',
        # Re-exports
        r'export\s+.*?\s+from\s+[\'"`]([^\'"`]+)[\'"`]',
        # JSX/TSX component imports in comments or strings
        r'\/\/\s*@filename:\s*([^\s]+)',
    ]
    
    for pattern in import_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            resolved_path = resolve_import_path(match, file_dir, pr_files)
            if resolved_path:
                dependencies.add(resolved_path)
    
    return dependencies

def parse_python_dependencies(content: str, file_dir: str, pr_files: Set[str]) -> Set[str]:
    """Parse Python import statements"""
    dependencies = set()
    
    # Python import patterns
    import_patterns = [
        r'from\s+([^\s]+)\s+import',
        r'import\s+([^\s,]+)',
        r'from\s+\.\s*([^\s]+)\s+import',  # Relative imports
        r'from\s+\.+([^\s]+)\s+import',    # Relative imports with dots
    ]
    
    for pattern in import_patterns:
        matches = re.findall(pattern, content, re.MULTILINE)
        for match in matches:
            # Convert Python module path to file path
            potential_paths = [
                match.replace('.', '/') + '.py',
                match.replace('.', '/') + '/__init__.py',
                match + '.py'
            ]
            
            for potential_path in potential_paths:
                resolved_path = resolve_import_path(potential_path, file_dir, pr_files)
                if resolved_path:
                    dependencies.add(resolved_path)
    
    return dependencies

def parse_css_dependencies(content: str, file_dir: str, pr_files: Set[str]) -> Set[str]:
    """Parse CSS @import statements"""
    dependencies = set()
    
    # CSS import patterns
    import_patterns = [
        r'@import\s+[\'"`]([^\'"`]+)[\'"`]',
        r'@import\s+url\s*\(\s*[\'"`]?([^\'"`\)]+)[\'"`]?\s*\)',
    ]
    
    for pattern in import_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for match in matches:
            resolved_path = resolve_import_path(match, file_dir, pr_files)
            if resolved_path:
                dependencies.add(resolved_path)
    
    return dependencies

def parse_html_dependencies(content: str, file_dir: str, pr_files: Set[str]) -> Set[str]:
    """Parse HTML script and link tags"""
    dependencies = set()
    
    # HTML dependency patterns
    patterns = [
        r'<script[^>]+src\s*=\s*[\'"`]([^\'"`]+)[\'"`]',
        r'<link[^>]+href\s*=\s*[\'"`]([^\'"`]+)[\'"`]',
        r'<img[^>]+src\s*=\s*[\'"`]([^\'"`]+)[\'"`]',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        for match in matches:
            # Skip external URLs
            if match.startswith(('http://', 'https://', '//', 'data:')):
                continue
            resolved_path = resolve_import_path(match, file_dir, pr_files)
            if resolved_path:
                dependencies.add(resolved_path)
    
    return dependencies

def parse_generic_dependencies(content: str, file_dir: str, pr_files: Set[str]) -> Set[str]:
    """Parse generic file references (for unknown file types)"""
    dependencies = set()
    
    # Look for any references to other files in the PR
    for pr_file in pr_files:
        # Check if the file is referenced by name (without extension)
        file_name = os.path.basename(pr_file)
        file_name_no_ext = os.path.splitext(file_name)[0]
        
        # Simple pattern matching for file references
        if (file_name in content or 
            file_name_no_ext in content or 
            pr_file in content):
            dependencies.add(pr_file)
    
    return dependencies

def resolve_import_path(import_path: str, base_dir: str, pr_files: Set[str]) -> Optional[str]:
    """
    Resolve an import path to an actual file in the PR.
    
    Args:
        import_path: The import path from the source code
        base_dir: Directory of the importing file  
        pr_files: Set of all files in the PR
    
    Returns:
        Resolved file path if found in PR, None otherwise
    """
    # Skip external packages (node_modules, absolute URLs, etc.)
    if (import_path.startswith(('http://', 'https://', '//', 'data:', '@', 'node_modules/')) or
        not import_path.startswith(('./', '../', '/')) and '/' not in import_path):
        return None
    
    # Normalize the import path
    if import_path.startswith('./'):
        # Relative to current directory
        candidate_path = os.path.normpath(os.path.join(base_dir, import_path[2:]))
    elif import_path.startswith('../'):
        # Relative to parent directory
        candidate_path = os.path.normpath(os.path.join(base_dir, import_path))
    elif import_path.startswith('/'):
        # Absolute path from project root
        candidate_path = import_path[1:]  # Remove leading slash
    else:
        # Try both relative and absolute
        candidate_path = os.path.normpath(os.path.join(base_dir, import_path))
    
    # Try different file extensions
    extensions = ['', '.js', '.jsx', '.ts', '.tsx', '.json', '.css', '.scss', '.py', '.html', '.htm']
    
    for ext in extensions:
        test_path = candidate_path + ext
        if test_path in pr_files:
            return test_path
        
        # Also try index files
        index_path = os.path.join(candidate_path, 'index' + ext)
        if index_path in pr_files:
            return index_path
    
    # Try exact match
    if candidate_path in pr_files:
        return candidate_path
    
    return None

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
        f"You are an expert AI code reviewer. Your job is to make ONLY ESSENTIAL corrections to the given file `{file_name}` "
        f"to ensure it meets the basic requirements and is free of errors. DO NOT over-engineer or modernize unnecessarily.\n\n"
        f"🚨 **CONSERVATIVE APPROACH REQUIRED:**\n"
        f"- Only make changes that are NECESSARY to fix actual problems\n"
        f"- Do NOT refactor working code unless it has clear issues\n"
        f"- Do NOT add new features or capabilities\n"
        f"- Do NOT modernize code that is already working\n"
        f"- Focus on: syntax errors, missing imports, type issues, obvious bugs\n"
        f"- Avoid: performance optimizations, pattern changes, style improvements\n\n"
        f"REQUIREMENTS FROM PROJECT:\n{requirements}\n\n"
        f"---\n Repository Context (other files for reference):\n{context}\n"
        f"---\n Current Code ({file_name} - {file_extension} file):\n```{file_extension}\n{code}\n```\n"
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
        
        # Track external dependencies from the processed file
        external_deps = extract_external_dependencies(file_name, updated_code)
        update_external_dependency_store(file_name, external_deps)
        
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
        
        # Fetch context using dynamic cache with dependency optimization
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

        # Print final summary with web search, dynamic context and dependency optimization benefits
        print(f"[Step3] 🎉 AI processing completed with WEB SEARCH, DEPENDENCY-OPTIMIZED context and intelligent processing!")
        print(f"[Step3] 📊 Processing Summary:")
        print(f"[Step3]   - Total files processed: {len(regenerated)}")
        print(f"[Step3]   - Regular files refined: {len(regular_files)}")
        print(f"[Step3]   - Package.json files analyzed: {len(package_json_files)}")
        print(f"[Step3]   - Web search enabled: {'✅ YES' if OPENAI_CLIENT else '❌ NO (OpenAI client not available)'}")
        print(f"[Step3]   - Context optimization: ✅ DEPENDENCY-BASED (only relevant imports)")
        print(f"[Step3]   - Dynamic context benefit: Later files used refined versions of earlier files")
        print(f"[Step3]   - Package.json strategy: ✅ DEPENDENCY SUMMARY (lightweight context instead of all files)")
        print(f"[Step3]   - External dependencies tracked: {len(EXTERNAL_DEPENDENCY_STORE)} files contributed to dependency analysis")
        print(f"[Step3]   - Processing order: Regular files first, package.json last with dependency summary")
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
                return extract_affected_files_from_error_fallback(build_error, package_dir_path)
            
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
                    return extract_affected_files_from_error_fallback(build_error, package_dir_path)
                    
            except json.JSONDecodeError as e:
                print(f"[LocalRepo] ❌ Could not parse LLM response as JSON: {e}")
                print(f"[LocalRepo] 🔍 Raw response: {response_text[:200]}...")
                return extract_affected_files_from_error_fallback(build_error, package_dir_path)
                
        except Exception as e:
            print(f"[LocalRepo] ❌ Error with LLM file identification: {e}")
            return extract_affected_files_from_error_fallback(build_error, package_dir_path)

    def extract_affected_files_from_error_fallback(build_error, package_dir_path):
        """Fallback regex-based file extraction (enhanced patterns)"""
        print(f"[LocalRepo] 🔄 Using fallback regex-based file identification...")
        affected_files = set()
        
        # Enhanced patterns to catch more error formats
        file_patterns = [
            # Standard TypeScript/JavaScript errors
            r'([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))\(\d+,\d+\):',  
            r'in ([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))',           
            r'([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json)):\d+:\d+',     
            r"'([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))'",
            
            # Enhanced patterns for build tool temporary files
            r'from ([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))\.timestamp',  # Vite timestamps
            r'imported from ([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))',     # Import errors
            r'Cannot find module.*?([a-zA-Z0-9_./\-]+\.(?:ts|tsx|js|jsx|vue|json))', # Module not found
            
            # Config file patterns
            r'config from ([a-zA-Z0-9_./\-]+\.(?:ts|js|json))',               # Config errors
            r'([a-zA-Z0-9_./\-]*tsconfig[a-zA-Z0-9_./\-]*\.json)',           # TypeScript configs
            r'([a-zA-Z0-9_./\-]*vite\.config\.(?:ts|js))',                   # Vite configs
            r'([a-zA-Z0-9_./\-]*webpack\.config\.(?:ts|js))',                # Webpack configs
        ]
        
        for pattern in file_patterns:
            matches = re.findall(pattern, build_error, re.IGNORECASE)
            for match in matches:
                # Clean up the file path
                file_path = match.strip()
                
                # Convert absolute paths to relative
                if file_path.startswith('/'):
                    try:
                        rel_path = os.path.relpath(file_path, package_dir_path)
                        if not rel_path.startswith('..'):
                            affected_files.add(rel_path)
                    except:
                        pass
                else:
                    # Assume it's already relative
                    affected_files.add(file_path)
        
        # Filter to only files that actually exist
        existing_files = []
        for file_path in affected_files:
            full_path = os.path.join(package_dir_path, file_path)
            if os.path.exists(full_path):
                existing_files.append(file_path)
        
        return existing_files

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
            try:
                affected_files = asyncio.run(extract_affected_files_from_error_with_llm(full_build_error, package_dir_path, pr_info))
            except Exception as e:
                print(f"[LocalRepo] ❌ Error with LLM file identification, using fallback: {e}")
                affected_files = extract_affected_files_from_error_fallback(full_build_error, package_dir_path)
            
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

async def fix_build_errors_with_web_search_v2(build_error, affected_files, package_dir_path, pr_info):
    """
    Enhanced version with comprehensive file type support and rename operations.
    Use OpenAI with web search to fix all types of build errors.
    Returns dict of corrected files or None if correction fails.
    """
    if not OPENAI_CLIENT:
        print("[LocalRepo] ⚠️ OpenAI client not available - falling back to regular LLM")
        return await fix_build_errors_with_llm(build_error, affected_files, package_dir_path, pr_info)
    
    if not pr_info:
        print("[LocalRepo] 🔧 No PR info available for web search build error correction")
        return None
    
    print(f"[LocalRepo] 🌐 Using enhanced web search to fix build errors...")
    
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
    
    # Create enhanced web search prompt
    files_context = ""
    for file_path, content in affected_file_contents.items():
        files_context += f"\n\n--- {file_path} ---\n```\n{content}\n```"
    
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
6. If files need to be renamed (like .js to .jsx), provide the corrected content

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
        
        # Enhanced patterns for extracting ALL file types and operations
        patterns = [
            # JavaScript/TypeScript files with explicit language tags
            r'```(?:javascript|typescript|jsx|tsx|js|ts)\s*(?://\s*)?([^\n]*\.(?:js|jsx|ts|tsx))[^\n]*\n([\s\S]*?)```',
            r'```(?:javascript|typescript|jsx|tsx|js|ts)\s*\n([\s\S]*?)```',
            
            # JSON config files (keep existing functionality)
            r'```json\s*(?://\s*)?([^\n]*\.json)[^\n]*\n([\s\S]*?)```',
            r'```json\s*\n([\s\S]*?)```',
            
            # File headers with code blocks (markdown style)
            r'#### ([^\n]*\.(?:js|jsx|ts|tsx|json|css|scss|html|vue))[^\n]*\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
            r'### ([^\n]*\.(?:js|jsx|ts|tsx|json|css|scss|html|vue))[^\n]*\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
            r'## ([^\n]*\.(?:js|jsx|ts|tsx|json|css|scss|html|vue))[^\n]*\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
            
            # Files mentioned in text followed by code blocks
            r'(?:update|fix|change|modify|create)\s+([^\s]+\.(?:js|jsx|ts|tsx|json|css|scss|html|vue))[^\n]*\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
            r'(?:file|component):\s*([^\s]+\.(?:js|jsx|ts|tsx|json|css|scss|html|vue))[^\n]*\n```[a-zA-Z0-9]*\n([\s\S]*?)```',
            
            # Direct file paths with code blocks
            r'([a-zA-Z0-9_./\-]+\.(?:js|jsx|ts|tsx|json|css|scss|html|vue))\s*:?\s*```[a-zA-Z0-9]*\n([\s\S]*?)```',
            
            # Config files by specific name
            r'(tsconfig\.(?:app|node)?\.json)\s*[:\n]+\s*```[a-zA-Z0-9]*\n([\s\S]*?)```',
            r'(vite\.config\.(?:js|ts))\s*[:\n]+\s*```[a-zA-Z0-9]*\n([\s\S]*?)```',
            r'(package\.json)\s*[:\n]+\s*```[a-zA-Z0-9]*\n([\s\S]*?)```',
            
            # Pattern for any JSON code block near mentions of config files
            r'(?:tsconfig\.(?:app|node)?\.json|TypeScript configuration)[\s\S]*?```json\n([\s\S]*?)```',
            
            # Generic code blocks (for single file scenarios)
            r'```[a-zA-Z0-9]*\n([\s\S]*?)```'
        ]
        
        # Track which files we've found corrections for
        found_files = set()
        
        # Process patterns to find file corrections
        for pattern in patterns:
            matches = re.findall(pattern, response_text, re.IGNORECASE)
            
            for match in matches:
                if len(match) == 2:  # (filename, content) tuple
                    file_path, file_content = match
                    file_path = file_path.strip()
                    file_content = file_content.strip()
                    
                    # Clean up file path
                    if file_path.startswith('./'):
                        file_path = file_path[2:]
                    elif file_path.startswith('/'):
                        file_path = file_path[1:]
                    
                    # Check if this file is one we're trying to fix
                    if file_path in affected_files or file_path in affected_file_contents:
                        # Validate content based on file type
                        is_valid = True
                        if file_path.endswith('.json'):
                            try:
                                json.loads(file_content)
                            except json.JSONDecodeError:
                                is_valid = False
                                continue
                        
                        if is_valid:
                            corrected_files[file_path] = file_content
                            found_files.add(file_path)
                            print(f"[LocalRepo] ✅ Web search provided correction for {file_path}")
                        
                else:  # Just content - match to affected files
                    file_content = match if isinstance(match, str) else match[0]
                    file_content = file_content.strip()
                    
                    # If there's only one affected file and we haven't found a correction yet
                    if len(affected_files) == 1 and not found_files:
                        target_file = affected_files[0]
                        
                        # Validate content based on file type
                        is_valid = True
                        if target_file.endswith('.json'):
                            try:
                                json.loads(file_content)
                            except json.JSONDecodeError:
                                is_valid = False
                                continue
                        
                        if is_valid:
                            corrected_files[target_file] = file_content
                            found_files.add(target_file)
                            print(f"[LocalRepo] ✅ Web search provided correction for {target_file} (single file match)")
            
            # If we found corrections with this pattern, stop trying other patterns
            if found_files:
                break
        
        # Special handling for file rename operations
        if not corrected_files:
            print(f"[LocalRepo] 🔍 No direct file corrections found, checking for file rename operations...")
            
            # Look for rename suggestions in the response
            rename_patterns = [
                r'rename\s+([^\s]+\.js)\s+to\s+([^\s]+\.jsx)',
                r'change\s+([^\s]+\.js)\s+to\s+([^\s]+\.jsx)',
                r'extension\s+of\s+([^\s]+\.js)\s+to\s+([^\s]+\.jsx)',
                r'([^\s]+\.js)\s+to\s+([^\s]+\.jsx)',
                r'name\s+the\s+file\s+with\s+the\s+\.jsx\s+.*?extension',
                r'use\s+the\s+\.jsx\s+.*?extension',
                # More flexible patterns for web search responses
                r'rename.*?([^\s]+\.js).*?([^\s]+\.jsx)',
                r'change.*?([^\s]+\.js).*?([^\s]+\.jsx)',
                r'([^\s]+\.js).*?\.jsx',
                r'\.js.*?\.jsx',
                # Generic JSX extension suggestions
                r'\.jsx\s+extension',
                r'use\s+\.jsx',
                r'\.jsx\s+files',
                r'rename.*?\.js.*?\.jsx',
                r'change.*?\.js.*?\.jsx',
            ]
            
            for pattern in rename_patterns:
                matches = re.findall(pattern, response_text, re.IGNORECASE)
                for match in matches:
                    if len(match) == 2:  # (old_file, new_file)
                        old_file, new_file = match
                        old_file = old_file.strip()
                        new_file = new_file.strip()
                        
                        # Clean up paths
                        if old_file.startswith('./'):
                            old_file = old_file[2:]
                        if new_file.startswith('./'):
                            new_file = new_file[2:]
                        
                        # Check if the old file is one we're trying to fix
                        if old_file in affected_files or old_file in affected_file_contents:
                            # Get current content and convert it
                            if old_file in affected_file_contents:
                                current_content = affected_file_contents[old_file]
                                
                                # Convert CommonJS to ES modules for .js to .jsx rename
                                if old_file.endswith('.js') and new_file.endswith('.jsx'):
                                    converted_content = convert_commonjs_to_es_modules(current_content)
                                    corrected_files[new_file] = converted_content
                                    corrected_files[old_file] = None  # Mark for removal
                                    print(f"[LocalRepo] ✅ Web search suggested rename: {old_file} → {new_file}")
                                    found_files.add(new_file)
                                    break
                    elif 'jsx' in match or 'extension' in match:
                        # Handle generic rename suggestions
                        for affected_file in affected_files:
                            if affected_file.endswith('.js') and affected_file in affected_file_contents:
                                new_file = affected_file.replace('.js', '.jsx')
                                current_content = affected_file_contents[affected_file]
                                converted_content = convert_commonjs_to_es_modules(current_content)
                                corrected_files[new_file] = converted_content
                                corrected_files[affected_file] = None  # Mark for removal
                                print(f"[LocalRepo] ✅ Web search suggested rename: {affected_file} → {new_file}")
                                found_files.add(new_file)
                                break
                
                # Additional check for any JSX-related suggestions in the response
                if not found_files and ('jsx' in response_text.lower() or 'extension' in response_text.lower()):
                    print(f"[LocalRepo] 🔍 Found JSX/extension suggestions in response, checking for .js files...")
                    for affected_file in affected_files:
                        if affected_file.endswith('.js') and affected_file in affected_file_contents:
                            new_file = affected_file.replace('.js', '.jsx')
                            current_content = affected_file_contents[affected_file]
                            converted_content = convert_commonjs_to_es_modules(current_content)
                            corrected_files[new_file] = converted_content
                            corrected_files[affected_file] = None  # Mark for removal
                            print(f"[LocalRepo] ✅ Web search suggested rename (generic): {affected_file} → {new_file}")
                            found_files.add(new_file)
                            break
                
                if found_files:
                    break
        
        # Enhanced debugging and fallback logic
        if corrected_files:
            print(f"[LocalRepo] 🎉 Web search successfully provided {len(corrected_files)} corrections")
            return corrected_files
        else:
            print(f"[LocalRepo] ❌ Could not extract corrected files from web search response")
            print(f"[LocalRepo] 🔍 Response preview: {response_text[:500]}...")
            print(f"[LocalRepo] 🔍 Looking for corrections for: {affected_files}")
            print(f"[LocalRepo] 🔍 Available file contents: {list(affected_file_contents.keys())}")
            
            # Enhanced debugging - show what patterns we found
            for i, pattern in enumerate(patterns[:3]):  # Show first 3 patterns
                pattern_matches = re.findall(pattern, response_text, re.IGNORECASE)
                if pattern_matches:
                    print(f"[LocalRepo] 🔍 Pattern {i+1} found {len(pattern_matches)} matches")
                    for j, match in enumerate(pattern_matches[:2]):  # Show first 2 matches
                        print(f"[LocalRepo] 🔍   Match {j+1}: {str(match)[:100]}...")
            
            # Debug rename patterns
            print(f"[LocalRepo] 🔍 Checking rename patterns...")
            for i, pattern in enumerate(rename_patterns):
                pattern_matches = re.findall(pattern, response_text, re.IGNORECASE)
                if pattern_matches:
                    print(f"[LocalRepo] 🔍 Rename pattern {i+1} found {len(pattern_matches)} matches")
                    for j, match in enumerate(pattern_matches[:2]):
                        print(f"[LocalRepo] 🔍   Rename match {j+1}: {str(match)[:100]}...")
            
            # Check for JSX/extension keywords
            jsx_keywords = ['jsx', 'extension', 'rename', 'change']
            found_keywords = [kw for kw in jsx_keywords if kw in response_text.lower()]
            if found_keywords:
                print(f"[LocalRepo] 🔍 Found keywords in response: {found_keywords}")
            
            # Fall back to regular LLM if web search didn't provide parseable results
            print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
            return await fix_build_errors_with_llm(build_error, affected_files, package_dir_path, pr_info)
            
    except Exception as e:
        print(f"[LocalRepo] ❌ Error during web search build error correction: {e}")
        print("[LocalRepo] 🔄 Falling back to regular LLM correction...")
        return await fix_build_errors_with_llm(build_error, affected_files, package_dir_path, pr_info)