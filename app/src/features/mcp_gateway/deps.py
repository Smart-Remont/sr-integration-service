from src.config import mcp_gateway_config

from .gateway import McpGateway

# One per worker: its token cache and HTTP client live as long as the process.
mcp_gateway = McpGateway(mcp_gateway_config)
