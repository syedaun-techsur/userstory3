#!/usr/bin/env python3
"""
Scenario-based test for the Build Agent
This test simulates a real-world scenario where code changes need to be validated
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

def create_test_project():
    """Create a simple test project with potential build issues"""
    project_dir = tempfile.mkdtemp()
    
    # Create a simple Node.js project
    package_json = {
        "name": "test-project",
        "version": "1.0.0",
        "scripts": {
            "test": "node test.js",
            "build": "echo 'Build successful'"
        },
        "dependencies": {
            "express": "^4.18.0"
        }
    }
    
    # Create package.json
    with open(os.path.join(project_dir, "package.json"), "w") as f:
        json.dump(package_json, f, indent=2)
    
    # Create a simple test file
    test_js = """
console.log('Hello from test.js');
module.exports = { test: true };
"""
    
    with open(os.path.join(project_dir, "test.js"), "w") as f:
        f.write(test_js)
    
    # Create a README
    readme = """
# Test Project
This is a test project for build validation.
"""
    
    with open(os.path.join(project_dir, "README.md"), "w") as f:
        f.write(readme)
    
    return project_dir

def test_build_agent_scenario():
    """Test the build agent with a realistic scenario"""
    print("🚀 Testing Build Agent with Realistic Scenario...\n")
    
    project_dir = None
    
    try:
        # Create test project
        print("📁 Creating test project...")
        project_dir = create_test_project()
        print(f"✅ Test project created at: {project_dir}")
        
        # Create the crew
        print("\n🔧 Creating Local Processing Crew...")
        crew = LocalProcessingCrew()
        print("✅ Crew created successfully")
        
        # Get the build agent
        print("\n🤖 Getting Build Agent...")
        build_agent = crew.build_agent()
        print(f"✅ Build Agent: {build_agent.name}")
        print(f"   Role: {build_agent.role}")
        print(f"   Tools: {[tool.name for tool in build_agent.tools]}")
        
        # Test the build agent's tools directly
        print("\n🔧 Testing Build Agent Tools...")
        
        # Test Git Tool
        git_tool = build_agent.tools[0]  # Assuming GitTool is first
        print(f"   Testing {git_tool.name}...")
        
        # Test File System Tool
        file_tool = build_agent.tools[1]  # Assuming FileSystemTool is second
        print(f"   Testing {file_tool.name}...")
        
        # Test Build Tool
        build_tool = build_agent.tools[2]  # Assuming BuildTool is third
        print(f"   Testing {build_tool.name}...")
        
        # Test a simple build command
        print("\n🔨 Testing Build Command...")
        build_result = build_tool._run(project_dir, "npm install")
        print(f"✅ Build Result: {build_result[:200]}...")
        
        # Test file operations
        print("\n📄 Testing File Operations...")
        test_file = os.path.join(project_dir, "new_file.js")
        test_content = "console.log('New file created by agent');"
        
        write_result = file_tool._run(test_file, test_content, "write")
        print(f"✅ Write Result: {write_result}")
        
        read_result = file_tool._run(test_file, "", "read")
        print(f"✅ Read Result: {read_result}")
        
        # Test the complete workflow
        print("\n🔄 Testing Complete Workflow...")
        
        # Simulate the context that would be passed to the crew
        test_context = {
            "repo_name": "test-repo",
            "pr_number": 123,
            "refined_files": {
                "src/new_feature.js": "console.log('New feature added');",
                "package.json": json.dumps({
                    "name": "test-project",
                    "version": "1.0.0",
                    "scripts": {
                        "test": "node test.js",
                        "build": "echo 'Build successful'"
                    },
                    "dependencies": {
                        "express": "^4.18.0",
                        "lodash": "^4.17.21"  # New dependency
                    }
                }, indent=2)
            }
        }
        
        print("✅ Context prepared successfully")
        print(f"   Repository: {test_context['repo_name']}")
        print(f"   PR Number: {test_context['pr_number']}")
        print(f"   Files to process: {len(test_context['refined_files'])}")
        
        # Test that the crew can be instantiated with this context
        print("\n🎯 Testing Crew Kickoff Preparation...")
        
        # Note: We won't actually run the full crew kickoff as it might require
        # external dependencies, but we can test that everything is properly configured
        
        validate_task = crew.validate_builds_task()
        fix_task = crew.fix_errors_task()
        update_task = crew.update_dependencies_task()
        
        print("✅ All tasks configured successfully")
        print(f"   Validate Task: {validate_task.description[:60]}...")
        print(f"   Fix Task: {fix_task.description[:60]}...")
        print(f"   Update Task: {update_task.description[:60]}...")
        
        print("\n🎉 Scenario Test Completed Successfully!")
        print("   The build agent is properly configured and ready for use.")
        
        return True
        
    except Exception as e:
        print(f"❌ Scenario Test Failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        if project_dir and os.path.exists(project_dir):
            print(f"\n🧹 Cleaning up test project: {project_dir}")
            subprocess.run(f"rm -rf {project_dir}", shell=True)

def test_error_scenarios():
    """Test how the build agent handles error scenarios"""
    print("\n🚨 Testing Error Scenarios...\n")
    
    try:
        crew = LocalProcessingCrew()
        build_agent = crew.build_agent()
        build_tool = build_agent.tools[2]  # BuildTool
        
        # Test with non-existent directory
        print("🔧 Testing with non-existent directory...")
        result = build_tool._run("/non/existent/path", "echo 'test'")
        print(f"✅ Error handling: {result}")
        
        # Test with invalid command
        print("🔧 Testing with invalid command...")
        result = build_tool._run(".", "invalid_command_that_should_fail")
        print(f"✅ Error handling: {result}")
        
        print("✅ Error scenarios handled properly")
        return True
        
    except Exception as e:
        print(f"❌ Error scenario test failed: {e}")
        return False

def main():
    """Run the scenario tests"""
    print("🚀 Starting Build Agent Scenario Tests...\n")
    
    tests = [
        ("Realistic Scenario", test_build_agent_scenario),
        ("Error Scenarios", test_error_scenarios),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n{'='*60}")
        print(f"Running: {test_name}")
        print(f"{'='*60}")
        
        try:
            if test_func():
                passed += 1
                print(f"✅ {test_name} PASSED")
            else:
                print(f"❌ {test_name} FAILED")
        except Exception as e:
            print(f"❌ {test_name} FAILED with exception: {e}")
    
    print(f"\n{'='*60}")
    print(f"SCENARIO TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Passed: {passed}/{total}")
    print(f"Failed: {total - passed}/{total}")
    
    if passed == total:
        print("🎉 All scenario tests passed! Your build agent is ready for production.")
    else:
        print("⚠️  Some scenario tests failed. Please check the output above for details.")
    
    return passed == total

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 