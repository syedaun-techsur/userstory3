## === UPDATED server.py using OpenAI GPT-4 ===
from mcp import Tool
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent
from openai import OpenAI
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize OpenAI client
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is required")

client = OpenAI(api_key=OPENAI_API_KEY)

# === LLM Interface using OpenAI GPT-4 ===
def get_llm_response(prompt: str, model_name: str = 'gpt-4.1') -> str:
    """Get response from OpenAI GPT-4.1 (1M token context window)"""
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are an expert AI code reviewer and developer. Provide clear, concise, and accurate code improvements."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=32768
        )
        content = response.choices[0].message.content
        return content.strip() if content else "No response generated"
    except Exception as e:
        return f"Error calling OpenAI API: {str(e)}"

# === MCP Server Setup ===
server = Server("codegen-server", version="1.0.0")

@server.list_tools()
async def handle_list_tools():
    return [
        Tool(
            name="codegen",
            description="Refine code using OpenAI GPT-4, based on user-provided prompt.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The prompt to send to the LLM for code generation/refinement"
                    }
                },
                "required": ["prompt"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    if name == "codegen":
        prompt = arguments.get("prompt", "")
        response = get_llm_response(prompt)
        return [TextContent(type="text", text=response)]
    else:
        raise ValueError(f"Unknown tool: {name}")

if __name__ == "__main__":
    import asyncio

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())
