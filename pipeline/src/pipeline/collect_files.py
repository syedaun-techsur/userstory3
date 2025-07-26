from github import Github
import os

def collect_files_for_refinement(repo_name: str, pr_number: int, pr_info=None):
    """Direct copy of the working file collection function"""
    github_direct = Github(os.getenv("GITHUB_TOKEN"))
    if not github_direct:
        print("GitHub API not available")
        return {}
    
    try:
        repo = github_direct.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        
        print(f"Got PR #{pr.number}: {pr.title}")
        
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
            
        print(f"Got {len(pr_files)} PR files")
        
        # Filter files
        file_names = set()
        for file in pr_files:
            # Skip lock files in any directory
            if file["filename"].endswith("package-lock.json") or file["filename"].endswith("package.lock.json"):
                print(f"Skipping lock file: {file['filename']}")
                continue
            # Skip GitHub workflow and config files
            if file["filename"].startswith('.github/'):
                print(f"Skipping GitHub workflow or config file: {file['filename']}")
                continue
            # Skip LICENSE files (various formats)
            if file["filename"].upper() in ['LICENSE', 'LICENSE.TXT', 'LICENSE.MD', 'LICENSE.MIT', 'LICENSE.APACHE', 'LICENSE.BSD']:
                print(f"Skipping license file: {file['filename']}")
                continue
            # Skip macOS .DS_Store files
            if file["filename"] == '.DS_Store' or file["filename"].endswith('/.DS_Store'):
                print(f"Skipping .DS_Store file: {file['filename']}")
                continue
            # Skip asset and binary files
            asset_extensions = [
                '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.bmp', '.tiff',
                '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm',
                '.mp3', '.wav', '.flac', '.aac', '.ogg',
                '.ttf', '.otf', '.woff', '.woff2', '.eot',
                '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                '.zip', '.rar', '.7z', '.tar', '.gz',
                '.exe', '.dll', '.so', '.dylib'
            ]
            if any(file["filename"].lower().endswith(ext) for ext in asset_extensions):
                print(f"Skipping asset/binary file: {file['filename']}")
                continue
            file_names.add(file["filename"])

        print(f"File names to process: {file_names}")
        
        # Get file contents
        result = {}
        ref = pr_info.get("pr_branch", pr.head.ref) if pr_info else pr.head.ref
        
        for file_name in file_names:
            try:
                print(f"Getting content for {file_name}...")
                file_content = repo.get_contents(file_name, ref=ref)
                
                if isinstance(file_content, list):
                    print(f"Skipping directory {file_name}")
                    continue
                else:
                    content = file_content.decoded_content.decode('utf-8')
                    result[file_name] = content
                    print(f"Successfully got content for {file_name}")
            except Exception as e:
                print(f"Error reading file {file_name}: {e}")
                continue

        print(f"Returning {len(result)} files")
        return result
        
    except Exception as e:
        print(f"Direct API failed: {e}")
        return {}