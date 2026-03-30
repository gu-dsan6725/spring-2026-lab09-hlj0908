"""
Simple Strands Agent with DuckDuckGo, Context7 MCP, and Braintrust Observability.

This agent demonstrates:
- DuckDuckGo web search tool
- Context7 MCP server integration
- Braintrust observability using OpenTelemetry
- Anthropic Claude Haiku via Strands
"""

import asyncio
import json
import logging
import os
from typing import Optional

from braintrust.otel import BraintrustSpanProcessor
from ddgs import DDGS
from dotenv import load_dotenv
from mcp.client.streamable_http import streamablehttp_client
from opentelemetry.sdk.trace import TracerProvider
from strands import Agent
from strands.tools.decorator import tool
from strands.tools.mcp import MCPClient


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)
logger = logging.getLogger(__name__)


# Load environment variables
# override=True is important so .env replaces any stale shell / inherited placeholders
load_dotenv(dotenv_path=".env", override=True)


def _get_env_var(key: str, default: Optional[str] = None) -> str:
    """Get environment variable or raise error if not found."""
    value = os.getenv(key, default)
    if value is None:
        raise ValueError(f"Environment variable {key} not set")
    return value


@tool
def duckduckgo_search(query: str, max_results: int = 5) -> str:
    """
    Search DuckDuckGo for the given query.

    Use this for:
    - current events
    - general web information
    - news
    - broad factual lookups

    Args:
        query: search query string
        max_results: maximum number of results to return

    Returns:
        JSON string containing search results
    """
    try:
        query = " ".join(str(query).splitlines()).strip()
        logger.info(f"Searching DuckDuckGo for: {query}")

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        logger.info(f"Found {len(results)} results")
        return json.dumps(results, indent=2).rstrip()

    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}")
        return json.dumps({"error": str(e)}).rstrip()


def create_streamable_http_transport():
    """Create streamable HTTP transport for the Context7 MCP server."""
    return streamablehttp_client("https://mcp.context7.com/mcp")


def _setup_observability() -> TracerProvider:
    """
    Set up OpenTelemetry with Braintrust for observability.

    Returns:
        Configured TracerProvider instance
    """
    logger.info("Setting up Braintrust observability")

    braintrust_api_key = _get_env_var("BRAINTRUST_API_KEY").strip()
    braintrust_project = _get_env_var("BRAINTRUST_PROJECT").strip()

    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(
        BraintrustSpanProcessor(
            api_key=braintrust_api_key,
            parent=braintrust_project,
        )
    )

    from opentelemetry import trace
    trace.set_tracer_provider(tracer_provider)

    logger.info(f"Braintrust observability configured for project: {braintrust_project}")
    return tracer_provider


def _create_agent() -> Agent:
    """
    Create and configure the Strands agent.

    Returns:
        Configured Agent instance
    """
    logger.info("Creating Strands agent")

    _setup_observability()

    anthropic_api_key = _get_env_var("ANTHROPIC_API_KEY").strip()
    os.environ["ANTHROPIC_API_KEY"] = anthropic_api_key

    # Optional debug preview
    logger.info(
        f"ANTHROPIC key preview: {repr(anthropic_api_key[:12])} ... len={len(anthropic_api_key)}"
    )

    system_prompt = """You are a helpful AI assistant with access to:
1. DuckDuckGo web search for current events, news, and general web information
2. Context7 MCP tools for programming documentation and library/framework usage

Tool routing rules:
- Use DuckDuckGo for news, current events, broad web search, and general information.
- Use Context7 MCP tools for technical documentation, API/library/framework usage, and programming questions.
- If asked what MCP tools are available, explain the loaded MCP tools clearly.
- For technical questions, prefer MCP documentation tools when applicable.
- Keep responses concise and useful.
- Do not include trailing whitespace at the end of any response.
"""

    from strands.models import AnthropicModel

    model = AnthropicModel(
        model_id="claude-3-haiku-20240307",
        max_tokens=4096,
    )

    streamable_http_mcp_client = MCPClient(create_streamable_http_transport)

    # Load MCP tools once during agent creation
    with streamable_http_mcp_client:
        logger.info("Connecting to Context7 MCP server...")
        mcp_tools = streamable_http_mcp_client.list_tools_sync()
        logger.info(f"Loaded {len(mcp_tools)} MCP tools from Context7")

        for tool_obj in mcp_tools:
            tool_name = getattr(tool_obj, "tool_name", None) or getattr(tool_obj, "name", None)
            logger.info(f"MCP tool loaded: {tool_name if tool_name else repr(tool_obj)}")

    tools = [duckduckgo_search] + mcp_tools

    agent = Agent(
        system_prompt=system_prompt,
        model=model,
        tools=tools,
    )

    logger.info("Agent created successfully with Braintrust observability and MCP tools")
    return agent


async def _run_agent_async(agent: Agent, user_input: str) -> str:
    """
    Run the agent asynchronously with the given input.

    Args:
        agent: The Strands agent instance
        user_input: User's question or prompt

    Returns:
        Agent response as string
    """
    user_input = " ".join(str(user_input).splitlines()).strip()
    logger.info(f"Processing user input: {user_input}")

    response = await agent.invoke_async(user_input)

    # Normalize output to avoid accidental trailing whitespace propagation
    response = str(response).rstrip()

    logger.info("Agent response generated")
    return response


def main() -> None:
    """Main function to run the agent."""
    logger.info("Starting Simple Agent with MCP + Observability")

    agent = _create_agent()

    print("\n" + "=" * 80)
    print("Simple Agent with MCP + Observability Demo")
    print("=" * 80)
    print()
    print("Ask me anything! I can search the web with DuckDuckGo and use Context7 MCP tools.")
    print("Type 'quit' to exit.")
    print()

    while True:
        try:
            user_input = input("You: ")
            user_input = " ".join(user_input.splitlines()).strip()

            if user_input.lower() in ["quit", "exit", "q"]:
                print("\nGoodbye!")
                break

            if not user_input:
                continue

            response = asyncio.run(_run_agent_async(agent, user_input))
            print(f"\nAgent: {response}\n")

        except EOFError:
            print("\n\nGoodbye!")
            break
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            logger.error(f"Error running agent: {e}")
            print(f"\nError: {e}\n")


if __name__ == "__main__":
    main()