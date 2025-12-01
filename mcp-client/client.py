import asyncio
import json
import os
import shlex
import subprocess
from typing import Optional
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from anthropic import Anthropic
from openai import AsyncOpenAI
from dotenv import load_dotenv

import sys


load_dotenv()  # load environment variables from .env

# source /Users/michaelnair/Desktop/random_projects/voting-info-agent/openai_key.bash
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

PATH_TO_VOTING_GUIDANCE_PROMPT = "prompts/voting_guidance_prompt.txt"
with open(PATH_TO_VOTING_GUIDANCE_PROMPT, "r", encoding="utf-8") as f:
    VOTING_GUIDANCE_PROMPT = f.read()

PATH_TO_USER_INTRO = "prompts/user_intro.txt"
with open(PATH_TO_USER_INTRO, "r", encoding="utf-8") as f:
    USER_INTRO_TEXT = f.read().strip()

import tiktoken
OPENAI_MODEL = "gpt-5-nano-2025-08-07"

# effective context window is the total context window(400000) divided by 5 (a number I pulled out of nowhere)
# TODO: research the best number to divide by or the proportion of the total context window openai models (especially smaller ones) can use without any degredation
EFFECTIVE_CONTEXT_WINDOW = 400000/5

def count_tokens(text: str, model: str = OPENAI_MODEL) -> int:
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        # Fallback encoding compatible with most newer OpenAI models
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))

class MCPClient:
    def __init__(self):
        # Initialize session and client objects
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.anthropic = Anthropic()
        self.openai = AsyncOpenAI()
    # methods will go here


    async def connect_to_server(self, server_script_path: str):
        """Connect to an MCP server

        Args:
            server_script_path: Path to the server script (.py or .js)
        """
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("Server script must be a .py or .js file")

        command = "python" if is_python else "node"
        server_params = StdioServerParameters(
            command=command,
            args=[server_script_path],
            env=None
        )

        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        self.stdio, self.write = stdio_transport
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))

        await self.session.initialize()

        # List available tools
        response = await self.session.list_tools()
        tools = response.tools
        print("\nConnected to server with tools:", [tool.name for tool in tools])


    async def process_query_anthropic(self, query: str) -> str:
        """Process a query using Claude and available tools"""
        messages = [
            {
                "role": "user",
                "content": query
            }
        ]

        response = await self.session.list_tools()
        available_tools = [{
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema
        } for tool in response.tools]

        # Initial Claude API call
        response = self.anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=messages,
            tools=available_tools
        )

        # Process response and handle tool calls
        final_text = []

        assistant_message_content = []
        for content in response.content:
            if content.type == 'text':
                final_text.append(content.text)
                assistant_message_content.append(content)
            elif content.type == 'tool_use':
                tool_name = content.name
                tool_args = content.input

                # Execute tool call
                result = await self.session.call_tool(tool_name, tool_args)
                final_text.append(f"[Calling tool {tool_name} with args {tool_args}]")

                assistant_message_content.append(content)
                messages.append({
                    "role": "assistant",
                    "content": assistant_message_content
                })
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": content.id,
                            "content": result.content
                        }
                    ]
                })

                # Get next response from Claude
                response = self.anthropic.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1000,
                    messages=messages,
                    tools=available_tools
                )

                final_text.append(response.content[0].text)

        return "\n".join(final_text)

    # TODO: add process_query_openai method
    async def process_query_openai(self, query: str) -> str:
        """Process a query using OpenAI and available tools"""
        if self.session is None:
            raise RuntimeError("Client session is not initialized.")

        messages = [
            {
                "role": "user",
                "content": query
            }
        ]

        response = await self.session.list_tools()
        available_tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                }
            }
            for tool in response.tools
        ]

        final_text: list[str] = []

        async def create_completion():
            kwargs = {
                "model": OPENAI_MODEL,
                "messages": messages
            }
            if available_tools:
                kwargs["tools"] = available_tools
            return await self.openai.chat.completions.create(**kwargs)

        completion = await create_completion()

        while True:
            choice = completion.choices[0]
            message = choice.message

            message_text = self._content_to_text(message.content)
            if message_text:
                final_text.append(message_text)

            tool_calls = message.tool_calls or []
            if not tool_calls:
                break

            if hasattr(message, "model_dump"):
                assistant_message = message.model_dump()
            else:
                assistant_message = {
                    "role": message.role,
                    "content": message.content,
                }
                if getattr(message, "tool_calls", None):
                    assistant_message["tool_calls"] = [
                        {
                            "id": tool_call.id,
                            "type": tool_call.type,
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments
                            }
                        }
                        for tool_call in tool_calls
                    ]
            if not assistant_message.get("content"):
                assistant_message["content"] = message_text or ""
            messages.append(assistant_message)

            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                arguments = tool_call.function.arguments or "{}"
                try:
                    parsed_args = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed_args = {}

                result = await self.session.call_tool(tool_name, parsed_args)
                result_content = getattr(result, "content", result)
                tool_response_text = self._content_to_text(result_content) or "Tool returned no content."

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_response_text
                    }
                )

            completion = await create_completion()

        return "\n".join(final_text)


    async def chat_loop(self):
        """Run an interactive chat loop"""
        print("\nMCP Client Started!")
        print("Type your queries or 'quit' to exit.")

        full_context = ""
        if VOTING_GUIDANCE_PROMPT:
            full_context = (
                "Voting guidance instructions:\n"
                f"{VOTING_GUIDANCE_PROMPT}\n\n"
            )
        
        # check what percentage of context has been used
        context_tokens = count_tokens(full_context, OPENAI_MODEL)
        # print the percentage of the recommended context window that has been used
        percentage_used = context_tokens / EFFECTIVE_CONTEXT_WINDOW * 100
        if percentage_used > 100:
            print(f"Warning: {percentage_used}% of the recommended context window has been used. It is recommended that you restart the chat and summarize your findings so far.")

        if USER_INTRO_TEXT:
            print(f"\n{USER_INTRO_TEXT}\n")

        while True:
            try:
                query = input("\nQuery: ").strip()

                if query.lower() == 'quit':
                    break
                
                full_context += f"User: {query}\n"
                query = full_context + query

                response = await self.process_query_openai(query)
                print("\n" + response)

                full_context += f"Assistant: {response}\n"

            except Exception as e:
                print(f"\nError: {str(e)}")

    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()

    @staticmethod
    def _content_to_text(content) -> str:
        """Convert mixed content structures into plain text"""
        if content is None:
            return ""

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = [MCPClient._content_to_text(item) for item in content]
            return "\n".join(part for part in parts if part)

        if isinstance(content, dict):
            if "text" in content and isinstance(content["text"], str):
                return content["text"]
            return json.dumps(content, default=str)

        if hasattr(content, "text"):
            text_value = getattr(content, "text")
            if isinstance(text_value, str):
                return text_value

        try:
            return json.dumps(content, default=str)
        except (TypeError, ValueError):
            return str(content)


async def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py <path_to_server_script>")
        sys.exit(1)

    client = MCPClient()
    try:
        await client.connect_to_server(sys.argv[1])
        print(f"Connected to server: {sys.argv[1]}")
        await client.chat_loop()
    finally:
        print("Failed to connect to server. Cleaning up...")
        await client.cleanup()

if __name__ == "__main__":
    asyncio.run(main())