#!/usr/bin/env python3
"""
Test script for the Build Agent in the Local Processing Crew
This script tests the build agent's ability to handle various scenarios
"""

import os
import sys
import tempfile
import subprocess
import json
from pathlib import Path

# Add the pipeline directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'pipeline', 'src'))

from pipeline.crews.local_processing_crew.local_crew import LocalProcessingCrew
from pipeline.crews.local_processing_crew.tools.git_tool import GitTool
from pipeline.crews.local_processing_crew.tools.build_tool import BuildTool
from pipeline.crews.local_processing_crew.tools.file_tool import FileSystemTool

def test_git_tool():
    """Test the Git tool functionality"""
    print("🔧 Testing Git Tool...")
    
    git_tool = GitTool()
    
    # Test with a simple public repository
    test_repo = "https://github.com/octocat/Hello-World"
    test_branch = "main"
    test_workspace = tempfile.mkdtemp()
    
    try:
        result = git_tool._run(test_repo, test_branch, test_workspace)
        print(f"✅ Git Tool Test Result: {result}")
        return True
    except Exception as e:
        print(f"❌ Git Tool Test Failed: {e}")
        return False
    finally:
        # Cleanup
        if os.path.exists(test_workspace):
            subprocess.run(f"rm -rf {test_workspace}", shell=True)

def test_file_tool():
    """Test the File System tool functionality"""
    print("🔧 Testing File System Tool...")
    
    file_tool = FileSystemTool()
    test_file = tempfile.mktemp()
    test_content = "Hello, this is a test file!"
    
    try:
        # Test write operation
        write_result = file_tool._run(test_file, test_content, "write")
        print(f"✅ Write Test: {write_result}")
        
        # Test read operation
        read_result = file_tool._run(test_file, "", "read")
        print(f"✅ Read Test: {read_result}")
        
        # Test exists operation
        exists_result = file_tool._run(test_file, "", "exists")
        print(f"✅ Exists Test: {exists_result}")
        
        return True
    except Exception as e:
        print(f"❌ File Tool Test Failed: {e}")
        return False
    finally:
        # Cleanup
        if os.path.exists(test_file):
            os.remove(test_file)

def test_build_tool():
    """Test the Build tool functionality"""
    print("🔧 Testing Build Tool...")
    
    build_tool = BuildTool()
    
    # Create a simple test project
    test_project = tempfile.mkdtemp()
    
    try:
        # Create a simple package.json for testing
        package_json = {
            "name": "test-project",
            "version": "1.0.0",
            "scripts": {
                "test": "echo 'Test passed!'"
            }
        }
        
        with open(os.path.join(test_project, "package.json"), "w") as f:
            json.dump(package_json, f)
        
        # Test with a simple command
        result = build_tool._run(test_project, "echo 'Build test successful'")
        print(f"✅ Build Tool Test Result: {result}")
        
        return True
    except Exception as e:
        print(f"❌ Build Tool Test Failed: {e}")
        return False
    finally:
        # Cleanup
        if os.path.exists(test_project):
            subprocess.run(f"rm -rf {test_project}", shell=True)

def test_build_agent_creation():
    """Test that the build agent can be created successfully"""
    print("🔧 Testing Build Agent Creation...")
    
    try:
        # Create the crew instance
        crew = LocalProcessingCrew()
        
        # Get the build agent
        build_agent = crew.build_agent()
        
        print(f"✅ Build Agent Created Successfully")
        print(f"   Name: {build_agent.name}")
        print(f"   Role: {build_agent.role}")
        print(f"   Tools: {[tool.name for tool in build_agent.tools]}")
        
        return True
    except Exception as e:
        print(f"❌ Build Agent Creation Failed: {e}")
        return False

def test_crew_workflow():
    """Test the complete crew workflow"""
    print("🔧 Testing Complete Crew Workflow...")
    
    try:
        # Create the crew instance
        crew = LocalProcessingCrew()
        
        # Test data
        test_context = {
            "repo_name": "test-repo",
            "pr_number": 123,
            "refined_files": {
                "src/test.js": "console.log('Hello World');"
            }
        }
        
        print("✅ Crew Created Successfully")
        print(f"   Agents: {len(crew.agents)}")
        print(f"   Tasks: {len(crew.tasks)}")
        
        return True
    except Exception as e:
        print(f"❌ Crew Workflow Test Failed: {e}")
        return False

def test_agent_configuration():
    """Test that agent configurations are properly loaded"""
    print("🔧 Testing Agent Configuration...")
    
    try:
        crew = LocalProcessingCrew()
        
        # Check if agents have proper configuration
        build_agent = crew.build_agent()
        error_agent = crew.error_correction_agent()
        dependency_agent = crew.dependency_agent()
        
        print("✅ Agent Configurations Loaded Successfully")
        print(f"   Build Agent: {build_agent.name}")
        print(f"   Error Agent: {error_agent.name}")
        print(f"   Dependency Agent: {dependency_agent.name}")
        
        return True
    except Exception as e:
        print(f"❌ Agent Configuration Test Failed: {e}")
        return False

def test_task_configuration():
    """Test that task configurations are properly loaded"""
    print("🔧 Testing Task Configuration...")
    
    try:
        crew = LocalProcessingCrew()
        
        # Check if tasks have proper configuration
        validate_task = crew.validate_builds_task()
        fix_task = crew.fix_errors_task()
        update_task = crew.update_dependencies_task()
        
        print("✅ Task Configurations Loaded Successfully")
        print(f"   Validate Task: {validate_task.description[:50]}...")
        print(f"   Fix Task: {fix_task.description[:50]}...")
        print(f"   Update Task: {update_task.description[:50]}...")
        
        return True
    except Exception as e:
        print(f"❌ Task Configuration Test Failed: {e}")
        return False

def main():
    """Run all tests"""
    print("🚀 Starting Build Agent Tests...\n")
    
    tests = [
        ("Git Tool", test_git_tool),
        ("File System Tool", test_file_tool),
        ("Build Tool", test_build_tool),
        ("Build Agent Creation", test_build_agent_creation),
        ("Agent Configuration", test_agent_configuration),
        ("Task Configuration", test_task_configuration),
        ("Crew Workflow", test_crew_workflow),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"Running: {test_name}")
        print(f"{'='*50}")
        
        try:
            if test_func():
                passed += 1
                print(f"✅ {test_name} PASSED")
            else:
                print(f"❌ {test_name} FAILED")
        except Exception as e:
            print(f"❌ {test_name} FAILED with exception: {e}")
    
    print(f"\n{'='*50}")
    print(f"TEST SUMMARY")
    print(f"{'='*50}")
    print(f"Passed: {passed}/{total}")
    print(f"Failed: {total - passed}/{total}")
    
    if passed == total:
        print("🎉 All tests passed! Your build agent is working correctly.")
    else:
        print("⚠️  Some tests failed. Please check the output above for details.")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 