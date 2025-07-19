import re
from typing import Set, Optional
import os
import json


def extract_maven_external_dependencies(content: str) -> Set[str]:
    """Extract external Maven dependencies from Java/Kotlin imports"""
    external_deps = set()
    
    # Pattern to match import statements
    import_patterns = [
        # Java imports: import org.springframework.boot.SpringApplication;
        r'import\s+([a-zA-Z_][a-zA-Z0-9_.]*);',
        # Static imports: import static org.junit.jupiter.api.Assertions.*;
        r'import\s+static\s+([a-zA-Z_][a-zA-Z0-9_.]*);',
        # Package declarations: package com.example.demo;
        r'package\s+([a-zA-Z_][a-zA-Z0-9_.]*);',
    ]
    
    for pattern in import_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE | re.MULTILINE)
        for match in matches:
            # Skip java.* and javax.* (standard Java packages)
            if match.startswith(('java.', 'javax.')):
                continue
            
            # Extract the main package name (first two parts for common Maven dependencies)
            parts = match.split('.')
            if len(parts) >= 2:
                # Common Maven dependency patterns
                if parts[0] in ['org', 'com', 'io', 'net'] and len(parts) >= 3:
                    package_name = f"{parts[0]}.{parts[1]}.{parts[2]}"
                else:
                    package_name = f"{parts[0]}.{parts[1]}"
                external_deps.add(package_name)
    
    return external_deps


def extract_pom_xml_external_dependencies(content: str, file_path: str) -> Set[str]:
    """Extract dependencies from pom.xml files"""
    external_deps = set()
    
    try:
        # Simple regex-based extraction for pom.xml dependencies
        # This is a conservative approach - only extract groupId:artifactId patterns
        
        # Pattern for dependencies section
        dependency_patterns = [
            # <groupId>org.springframework.boot</groupId>
            # <artifactId>spring-boot-starter-web</artifactId>
            r'<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>',
            # <dependency>...</dependency> blocks
            r'<dependency>.*?<groupId>([^<]+)</groupId>.*?<artifactId>([^<]+)</artifactId>.*?</dependency>',
        ]
        
        for pattern in dependency_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
            for match in matches:
                if isinstance(match, tuple) and len(match) >= 2:
                    group_id = match[0].strip()
                    artifact_id = match[1].strip()
                    dependency_name = f"{group_id}:{artifact_id}"
                    external_deps.add(dependency_name)
                elif isinstance(match, str):
                    # Handle single group matches
                    external_deps.add(match.strip())
    
    except Exception as e:
        print(f"[Step3] ⚠️ Error extracting dependencies from {file_path}: {e}")
    
    return external_deps

def extract_external_dependencies(file_path: str, file_content: str) -> Set[str]:
    """
    Extract external dependencies (npm packages or Maven dependencies) from a file's import statements.
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
        elif file_ext in ['java', 'kt']:
            external_deps.update(extract_maven_external_dependencies(file_content))
        elif file_path.endswith('pom.xml'):
            external_deps.update(extract_pom_xml_external_dependencies(file_content, file_path))
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
        
        elif file_ext in ['json'] and file_path.endswith('package.json'):
            # Special case: package.json files don't have local dependencies
            print(f"[Step3] 📦 package.json detected - no local dependencies to parse")
            return set()  # Empty set - handled separately with dependency summary
        
    
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
