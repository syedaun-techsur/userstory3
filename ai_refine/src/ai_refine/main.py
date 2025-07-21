#!/usr/bin/env python
import sys
import warnings
import os
from datetime import datetime

from ai_refine.crew import AiRefine

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# This main file is intended to be a way for you to run your
# crew locally, so refrain from adding unnecessary logic into this file.
# Replace with inputs you want to test with, it will automatically
# interpolate any tasks and agents information

def run():
    """
    Run the AI Refine crew with GitHub MCP integration.
    """
    # Default inputs for testing - you can modify these
    inputs = {
        'repo_name': 'octocat/Hello-World',  # Repository to analyze
        'pr_number': 1,                      # PR number to analyze
        'current_year': str(datetime.now().year)
    }
    
    # Check if GitHub token is available
    if not os.getenv("GITHUB_TOKEN"):
        print("❌ GITHUB_TOKEN environment variable not set")
        print("Please set your GitHub token: export GITHUB_TOKEN=your_token")
        return
    
    print("🚀 Starting AI Refine Crew with GitHub MCP Integration")
    print("=" * 60)
    print(f"📋 Repository: {inputs['repo_name']}")
    print(f"🔢 PR Number: {inputs['pr_number']}")
    print("=" * 60)
    
    try:
        result = AiRefine().crew().kickoff(inputs=inputs)
        print("\n✅ AI Refine crew completed successfully!")
        print("📊 Results:")
        print("=" * 40)
        print(result)
        
    except Exception as e:
        print(f"❌ An error occurred while running the crew: {e}")
        import traceback
        traceback.print_exc()


def run_with_custom_inputs():
    """
    Run the crew with custom repository and PR inputs.
    Usage: python main.py custom <repo_name> <pr_number>
    Example: python main.py custom microsoft/vscode 200000
    """
    if len(sys.argv) < 4:
        print("❌ Usage: python main.py custom <repo_name> <pr_number>")
        print("Example: python main.py custom microsoft/vscode 200000")
        return
    
    repo_name = sys.argv[2]
    pr_number = int(sys.argv[3])
    
    inputs = {
        'repo_name': repo_name,
        'pr_number': pr_number,
        'current_year': str(datetime.now().year)
    }
    
    # Check if GitHub token is available
    if not os.getenv("GITHUB_TOKEN"):
        print("❌ GITHUB_TOKEN environment variable not set")
        print("Please set your GitHub token: export GITHUB_TOKEN=your_token")
        return
    
    print("🚀 Starting AI Refine Crew with Custom Inputs")
    print("=" * 60)
    print(f"📋 Repository: {inputs['repo_name']}")
    print(f"🔢 PR Number: {inputs['pr_number']}")
    print("=" * 60)
    
    try:
        result = AiRefine().crew().kickoff(inputs=inputs)
        print("\n✅ AI Refine crew completed successfully!")
        print("📊 Results:")
        print("=" * 40)
        print(result)
        
    except Exception as e:
        print(f"❌ An error occurred while running the crew: {e}")
        import traceback
        traceback.print_exc()


def show_help():
    """
    Show help information for the AI Refine crew.
    """
    print("🌟 AI Refine Crew - GitHub MCP Integration")
    print("=" * 50)
    print("Available commands:")
    print("  run                    - Run with default repository (octocat/Hello-World PR #1)")
    print("  custom <repo> <pr>     - Run with custom repository and PR")
    print("  test-agents            - Test individual agents and their tools")
    print("  test-tasks             - Test individual tasks and their configuration")
    print("  train <iter> <file>    - Train the crew")
    print("  replay <task_id>       - Replay from specific task")
    print("  test <iter> <llm>      - Test the crew")
    print("  help                   - Show this help message")
    print("\nExamples:")
    print("  python main.py run")
    print("  python main.py custom microsoft/vscode 200000")
    print("  python main.py test-agents")
    print("  python main.py test-tasks")
    print("\nRequirements:")
    print("  - GITHUB_TOKEN environment variable must be set")
    print("  - All dependencies installed (crewai, crewai-tools[mcp], etc.)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        show_help()
    elif sys.argv[1] == "run":
        run()
    elif sys.argv[1] == "custom":
        run_with_custom_inputs()
    elif sys.argv[1] == "help":
        show_help()
    else:
        print(f"❌ Unknown command: {sys.argv[1]}")
        show_help()
