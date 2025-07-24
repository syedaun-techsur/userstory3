import re

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
    # Handle both "### Changes:\n" and "### Changes: " formats
    changes_match = re.search(r"### Changes:\s*\n([\s\S]*?)(?=\n```[a-zA-Z0-9]*\n|### Updated Code:|$)", response, re.IGNORECASE)
    if changes_match:
        changes = changes_match.group(1).strip()
    else:
        # If not found outside, look inside <think> block
        think_match = re.search(r"<think>([\s\S]*?)</think>", response, re.IGNORECASE)
        if think_match:
            think_content = think_match.group(1)
            changes_match = re.search(r"### Changes:\s*\n([\s\S]*?)(?=\n```[a-zA-Z0-9]*\n|### Updated Code:|$)", think_content, re.IGNORECASE)
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