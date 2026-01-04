import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

from app.config import PROJECT_ROOT
from app.logger import logger
from app.tool.base import BaseTool, ToolResult


class GoogleSearchItem(BaseModel):
    """Represents a single Google Custom Search result"""

    title: str = Field(description="The title of the search result")
    link: str = Field(description="The URL of the search result")
    snippet: str = Field(description="A description or snippet of the search result")

    def __str__(self) -> str:
        """String representation of a search result item."""
        return f"{self.title} - {self.link}\n{self.snippet}"


class GoogleCustomSearchTool(BaseTool):
    """
    Google Custom Search API tool for web searching.

    Uses the official Google Custom Search JSON API to perform web searches.
    Requires API Key and Custom Search Engine ID configured in config.toml.
    """

    name: str = "google_custom_search"
    description: str = """Search the web using Google Custom Search API.
    This tool provides high-quality search results from Google with structured data including titles, URLs, and snippets.
    Requires Google Custom Search API credentials configured in config.toml."""
    parameters: dict = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "(required) The search query to submit to Google.",
            },
        },
        "required": ["query"],
    }

    # Cache for configuration
    _config_cache: Optional[Dict[str, Any]] = None

    def _load_config(self) -> Dict[str, Any]:
        """Load Google Custom Search configuration from config.toml"""
        if self._config_cache is not None:
            return self._config_cache

        config_path = PROJECT_ROOT / "config" / "config.toml"
        if not config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {config_path}. "
                "Please ensure config/config.toml exists with [google_custom_search] section."
            )

        with config_path.open("rb") as f:
            raw_config = tomllib.load(f)

        google_config = raw_config.get("google_custom_search", {})
        if not google_config:
            raise ValueError(
                "Missing [google_custom_search] section in config.toml. "
                "Please add API Key and CSE ID configuration."
            )

        # Validate required fields
        api_key = google_config.get("api_key", "")
        cse_id = google_config.get("cse_id", "")

        if not api_key or api_key == "YOUR_API_KEY_HERE":
            raise ValueError(
                "Google Custom Search API Key not configured. "
                "Please set 'api_key' in [google_custom_search] section of config.toml"
            )

        if not cse_id or cse_id == "YOUR_CSE_ID_HERE":
            raise ValueError(
                "Google Custom Search Engine ID not configured. "
                "Please set 'cse_id' in [google_custom_search] section of config.toml"
            )

        self._config_cache = {
            "api_key": api_key,
            "cse_id": cse_id,
            "num_results": google_config.get("num_results", 10),
            "date_restrict": google_config.get("date_restrict", ""),
        }

        return self._config_cache

    async def execute(self, query: str) -> ToolResult:
        """
        Execute a Google Custom Search query.

        Args:
            query: The search query string

        Returns:
            ToolResult containing search results or error message
        """
        try:
            # Load configuration
            config = self._load_config()
            api_key = config["api_key"]
            cse_id = config["cse_id"]
            num_results = config["num_results"]
            date_restrict = config["date_restrict"]

            logger.info(f"Executing Google Custom Search: {query}")

            # Prepare API request parameters
            params = {
                "key": api_key,
                "cx": cse_id,
                "q": query,
                "num": min(num_results, 10),  # API limit is 10 per request
            }

            # Add optional date restriction if configured
            if date_restrict:
                params["dateRestrict"] = date_restrict

            # Make API request
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params=params,
                )

                # Check for HTTP errors
                if response.status_code != 200:
                    error_msg = f"Google API returned status {response.status_code}"
                    try:
                        error_data = response.json()
                        if "error" in error_data:
                            error_detail = error_data["error"].get("message", "")
                            error_msg = f"{error_msg}: {error_detail}"
                    except Exception:
                        pass

                    logger.error(error_msg)
                    return ToolResult(error=error_msg)

                # Parse response
                data = response.json()

                # Check if there are any results
                items = data.get("items", [])
                if not items:
                    logger.info(f"No search results found for query: {query}")
                    return ToolResult(
                        output=f"No search results found for query: '{query}'"
                    )

                # Extract search results
                search_results = []
                for item in items:
                    search_item = GoogleSearchItem(
                        title=item.get("title", "No title"),
                        link=item.get("link", ""),
                        snippet=item.get("snippet", "No description available"),
                    )
                    search_results.append(search_item)

                # Format output
                output_lines = [f"Google Search results for '{query}':\n"]
                for i, result in enumerate(search_results, 1):
                    output_lines.append(f"{i}. {result.title}")
                    output_lines.append(f"   URL: {result.link}")
                    output_lines.append(f"   Snippet: {result.snippet}")
                    output_lines.append("")  # Empty line between results

                # Add search metadata
                search_info = data.get("searchInformation", {})
                total_results = search_info.get("totalResults", "Unknown")
                search_time = search_info.get("searchTime", "Unknown")
                output_lines.append(
                    f"Total results: {total_results} (search time: {search_time}s)"
                )

                output_text = "\n".join(output_lines)
                logger.info(
                    f"Google Custom Search completed: {len(search_results)} results returned"
                )

                return ToolResult(output=output_text)

        except FileNotFoundError as e:
            error_msg = f"Configuration error: {str(e)}"
            logger.error(error_msg)
            return ToolResult(error=error_msg)

        except ValueError as e:
            error_msg = f"Configuration error: {str(e)}"
            logger.error(error_msg)
            return ToolResult(error=error_msg)

        except httpx.TimeoutException:
            error_msg = "Google API request timed out. Please try again."
            logger.error(error_msg)
            return ToolResult(error=error_msg)

        except httpx.HTTPError as e:
            error_msg = f"HTTP error occurred: {str(e)}"
            logger.error(error_msg)
            return ToolResult(error=error_msg)

        except Exception as e:
            error_msg = f"Unexpected error during Google search: {str(e)}"
            logger.error(error_msg)
            return ToolResult(error=error_msg)


if __name__ == "__main__":
    # Simple test
    import asyncio

    async def test_search():
        tool = GoogleCustomSearchTool()
        result = await tool.execute(query="成都飞泰国今天机票价格")
        print(result)

    asyncio.run(test_search())
