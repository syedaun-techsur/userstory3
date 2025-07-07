# AI Code Refinement Pipeline

An automated system that uses AI to refine code in GitHub Pull Requests, validate changes locally, and push improvements to new branches.

## 🏗️ Architecture Overview

```
GitHub PR Event → Webhook → PRWatcher → Step 3 (AI + Local) → Step 4 (GitHub Push) → New PR
```

## 🚀 Quick Start

### Prerequisites
- Python 3.13+
- Node.js and npm (for dependency resolution)
- GitHub Token with repo access
- OpenAI API Key

### Installation
```bash
git clone <repository-url>
cd <repository-name>
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Environment Variables
Create a `.env` file:
```env
GITHUB_TOKEN=your_github_token_here
OPENAI_API_KEY=your_openai_api_key_here
```

### Dependencies
```
langchain-openai>=0.0.2
python-dotenv>=1.0.0
PyGithub>=2.1.1
mcp>=0.1.0 
Flask
GitPython>=3.1.0
```

## 📋 Detailed Flow Documentation

### **1. PR Event Trigger**
```
GitHub PR opened/updated → Webhook Server → PRWatcher.handle_new_pr()
```

**Entry Point**: `PRWatcher.handle_new_pr(repo_name, pr_number, pr_title, pr_head_ref, pr_base_ref)`

**Parameters**:
- `repo_name`: `"username/repository"`
- `pr_number`: `123`
- `pr_title`: `"Add user authentication"`
- `pr_head_ref`: `"feature/auth"` (PR branch)
- `pr_base_ref`: `"main"` (target branch)

**Creates PR Info Object**:
```python
pr_info = {
    "repo_name": "username/repository",
    "pr_number": 123,
    "pr_title": "Add user authentication", 
    "pr_branch": "feature/auth",
    "main_branch": "main"
}
```

---

### **2. Step 3: AI Processing + Local Validation**

#### **2.1 File Collection**
**Function**: `regenerate_files(pr_info)` → `collect_files_for_refinement(repo_name, pr_number, pr_info)`

**Process**:
- Gets all changed files in PR via GitHub API
- Filters out lock files (`package-lock.json`) and `.github/` files
- Fetches file content from PR branch

**Returns**:
```python
files_for_update = {
    "src/App.js": "import React from 'react';\nfunction App() { return <div>Hello</div>; }",
    "package.json": "{\n  \"name\": \"my-app\",\n  \"dependencies\": {...}\n}",
    "src/components/Button.tsx": "interface ButtonProps {...}"
}
```

#### **2.2 Requirements Extraction**
**Function**: `fetch_requirements_from_readme(repo_name, branch)`

**Purpose**: Extracts coding standards from README.md to guide AI refinement

**Returns**: README.md content as coding standards string

#### **2.3 AI Processing Pipeline**
**Function**: `regenerate_code_with_mcp(files_for_update, requirements_text, pr, pr_info)`

**For each file, the following functions are called**:

1. **Context Building**: `fetch_repo_context(repo_name, pr_number, target_file, pr_info)`
   - Gets content of OTHER files in PR for context
   - Excludes current file being processed
   - Provides AI with understanding of the broader codebase
   
2. **Prompt Creation**: `compose_prompt(requirements, old_code, file_name, context)`
   - Combines requirements + file code + context
   - Creates detailed AI instructions with specific formatting requirements
   
3. **AI Processing**: `process_single_file(session, file_name, old_code, requirements, pr_info)`
   - **MCP Call**: `session.call_tool("codegen", arguments={"prompt": prompt})`
   - **Server**: `server.py` handles OpenAI GPT-4.1 Mini API communication
   - **Timeout**: 5-minute timeout per file
   - **Returns**: AI response + token usage information
   
4. **Response Parsing**:
   - `extract_response_content(result, file_name)`: Gets AI's text response
   - `parse_token_usage(result)`: Extracts token counts for pricing calculation
   - `extract_changes(response, file_name)`: Finds "### Changes:" section  
   - `extract_updated_code(response)`: Finds "### Updated Code:" section
   - `cleanup_extracted_code(updated_code)`: Removes formatting artifacts

**AI Processing Result Structure**:
```python
regenerated_files = {
    "src/App.js": {
        "old_code": "import React from 'react';\nfunction App() { return <div>Hello</div>; }",
        "changes": "- Added TypeScript types\n- Improved error handling", 
        "updated_code": "import React from 'react';\n\ninterface AppProps {}\n\nconst App: React.FC<AppProps> = () => {\n  return <div>Hello</div>;\n};\n\nexport default App;"
    },
    "package.json": {
        "old_code": "{\"name\": \"my-app\", ...}",
        "changes": "- Updated React to latest version\n- Added TypeScript dependencies",
        "updated_code": "{\"name\": \"my-app\", \"dependencies\": {\"react\": \"^18.0.0\", \"typescript\": \"^5.0.0\"}, ...}"
    }
}
```

#### **2.4 Local Workspace Processing**
**Function**: `process_pr_with_local_repo(pr_info, regenerated_files)`

**Process**:

1. **Workspace Setup**: `get_persistent_workspace(repo_name, pr_branch, pr_number)`
   - Creates: `workspace/username_repository_PR123/`
   - Persistent across runs for efficiency
   
2. **Repository Cloning**:
   ```python
   # Clone original PR branch (not AI-refined)
   repo_url = f"https://{GITHUB_TOKEN}@github.com/{REPO_NAME}.git"
   repo = Repo.clone_from(repo_url, workspace_dir, branch=PR_BRANCH)
   ```
   
3. **Apply AI Changes**:
   ```python
   # Write AI-refined code to local files
   for file_path, file_data in regenerated_files.items():
       local_file_path = os.path.join(repo_path, file_path)
       os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
       with open(local_file_path, "w", encoding="utf-8") as f:
           f.write(file_data["updated_code"])  # String → File
   ```
   
4. **Dependency Resolution** (if `package.json` changed):
   ```python
   # Run npm install to generate lockfile
   result = subprocess.run(["npm", "install"], cwd=repo_path, timeout=300)
   
   # Read generated package-lock.json back to string
   with open(lockfile_path, "r") as f:
       lockfile_content = f.read()
   
   # Add lockfile to regenerated_files for GitHub push
   regenerated_files["package-lock.json"] = {
       "old_code": "",
       "changes": "Regenerated lockfile after package.json update via npm install",
       "updated_code": lockfile_content  # File → String
   }
   ```

**Enhanced Result After Local Processing**:
```python
regenerated_files = {
    # ... previous AI-refined files ...
    "package-lock.json": {  # Added by local processing
        "old_code": "",
        "changes": "Regenerated lockfile after package.json update via npm install",
        "updated_code": "{\n  \"name\": \"my-app\",\n  \"lockfileVersion\": 3,\n  \"requires\": true,\n  ..."
    }
}
```

---

### **3. Step 4: GitHub Push**

**Function**: `commit_regenerated_files(pr_info, regenerated_files)`

**Process**:

1. **Branch Management**:
   ```python
   BASE_BRANCH = "feature/auth"  # Original PR branch
   TARGET_BRANCH = "ai_refined_code_feature/auth"  # New branch for AI changes
   ```
   
2. **Create Target Branch** (if doesn't exist):
   - Gets SHA of base branch via `github_client.get_branch()`
   - Creates new branch from that SHA via `github_client.create_branch()`
   
3. **Push Each File**:
   ```python
   for fname, data in regenerated_files.items():
       old_code = data.get('old_code', '')
       updated_code = data.get('updated_code', '')
       
       # Skip if no real changes (normalized comparison)
       if normalize_code(old_code) == normalize_code(updated_code):
           continue
           
       # Create commit message from AI changes
       commit_message = f"AI Refactor for {fname}:\n\nChanges:\n{data.get('changes', 'No changes described.')}"
       
       # Push directly to GitHub via API (handles both update and create)
       github_client.update_file(
           repo_name=REPO_NAME,
           file_path=fname,
           message=commit_message,
           content=updated_code,  # String → GitHub
           branch=TARGET_BRANCH
       )
   ```
   
4. **Create Pull Request**:
   - **From**: `ai_refined_code_feature/auth`
   - **To**: `feature/auth` (original PR branch)
   - **Title**: "AI Refactored Code Update"
   - **Body**: "This PR includes updated code based on coding standards with inline changes described."

---

## 🔧 Key Technologies & Components

### **MCP (Model Context Protocol)**
- **Server**: `server.py` - Handles OpenAI API communication
- **Client**: Used in `step3_regenerate.py` to call AI
- **Tool**: `"codegen"` - AI code refinement tool using GPT-4.1 Mini

### **GitHub Integration**
- **MCP Client**: `github_mcp_client.py` - Async GitHub API wrapper
- **Operations**: Get PR files, file content, create branches, push files, create PRs
- **Authentication**: Uses GitHub token for all operations

### **Local Processing**
- **GitPython**: Repository cloning and Git operations
- **Subprocess**: Running `npm install`, dependency resolution
- **File System**: Writing/reading local files, directory management

### **AI Processing**
- **OpenAI GPT-4.1 Mini**: Code refinement model
- **Token Tracking**: Automatic usage monitoring and cost calculation
- **Context Window**: 1M+ tokens (≈4M characters) for large codebases

---

## 📊 Data Flow Summary

```
GitHub PR Files (API) 
    ↓ (strings)
AI Processing (OpenAI GPT-4.1 Mini)
    ↓ (enhanced strings)
Local Workspace (validation + dependency resolution)
    ↓ (validated strings + generated files)
GitHub Push (API)
    ↓
New PR with AI-refined code
```

### **Key Data Transformations**:
1. **GitHub API → Strings**: File content fetched as strings
2. **Strings → AI → Enhanced Strings**: Code improvement via AI
3. **Strings → Local Files**: Materialization for validation
4. **Local Files → Enhanced Strings**: Dependency artifacts added
5. **Enhanced Strings → GitHub API**: Final push to new branch

---

## 🧪 Future: Test Generation Integration

**Planned Addition** in `process_pr_with_local_repo()`:

```python
# TODO: Future user story - Generate and run tests here
test_files = generate_test_cases(repo_path, regenerated_files)
regenerated_files.update({
    "src/__tests__/App.test.js": {
        "old_code": "",
        "changes": "- Generated comprehensive unit tests for App component",
        "updated_code": "describe('App', () => {\n  test('renders without crashing', () => {\n    // Jest test code\n  });\n});"
    },
    "tests/e2e/app.spec.js": {
        "old_code": "",
        "changes": "- Generated Selenium E2E tests for App functionality", 
        "updated_code": "const { Builder, By } = require('selenium-webdriver');\n// Selenium test code"
    },
    "features/app.feature": {
        "old_code": "",
        "changes": "- Generated Cucumber scenarios for App behavior",
        "updated_code": "Feature: App functionality\n  Scenario: User loads app\n    # Cucumber scenarios"
    }
})

# Run tests with Jest, Selenium, Cucumber
# run_jest_tests(repo_path)
# run_selenium_tests(repo_path) 
# run_cucumber_tests(repo_path)
```

**Benefits**:
- Tests automatically generated alongside refined code
- Same `step4_commit.py` logic handles test files (file-agnostic design)
- Complete test coverage for AI-improved code

---

## 🔍 Audit & Logging

### **Audit Trail**
**Function**: `AuditLogger.log_feedback_cycle()` - Comprehensive logging for compliance:

**Logged Information**:
- Original code → AI changes → Final code
- Processing timestamps and duration
- Token usage and API costs
- Processing status and errors
- PR and branch information

**Audit Database Schema**:
- `processing_runs`: Overall processing sessions
- `file_processing`: Individual file transformations
- `feedback_cycles`: Complete audit trails per file

### **Error Handling & Monitoring**
- Graceful fallbacks at each step
- Detailed error logging with context
- Timeout handling for long-running operations
- API rate limit awareness

---

## 🚦 Usage Examples

### **Running the Pipeline**
```python
# Example: Process a specific PR
pr_watcher = PRWatcher()
pr_watcher.handle_new_pr(
    repo_name="myorg/myrepo",
    pr_number=123,
    pr_title="Add user authentication",
    pr_head_ref="feature/auth", 
    pr_base_ref="main"
)
```

### **Manual File Processing**
```python
# Example: Process specific files
pr_info = {
    "repo_name": "myorg/myrepo",
    "pr_number": 123,
    "pr_branch": "feature/auth",
    "main_branch": "main"
}

regenerated_files = regenerate_files(pr_info)
commit_regenerated_files(pr_info, regenerated_files)
```

---

## 🛠️ Configuration

### **AI Model Settings** (`server.py`)
```python
model_name = 'gpt-4.1-mini'  # 1M+ token context window
temperature = 0.1            # Low temperature for consistent code generation
max_tokens = 32768          # GPT-4.1 Mini max output tokens
```

### **Processing Limits** (`step3_regenerate.py`)
```python
MAX_CONTEXT_CHARS = 4000000  # 4M chars ≈ 1M tokens
timeout = 300               # 5 minutes per file AI processing
```

### **Workspace Management** (`step3_regenerate.py`)
```python
workspace_base = "workspace"                           # Base directory
workspace_dir = f"{repo_name}_PR{pr_number}"          # Per-PR isolation
```

---

## 🔒 Security Considerations

- **GitHub Token**: Requires repo-level access for file operations
- **OpenAI API Key**: Secure storage and rotation recommended
- **Code Exposure**: AI processes see full codebase content
- **Branch Isolation**: AI changes pushed to separate branches for review
- **Audit Trail**: Complete logging for security compliance

---


## 📈 Performance & Scaling

### **Token Usage & Costs**
- **Input**: ~$0.42 per 1M tokens (prompts + context)
- **Output**: ~$1.68 per 1M tokens (AI responses)

