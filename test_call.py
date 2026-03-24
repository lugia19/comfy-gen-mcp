"""Quick test script to call the MCP server's generate_image tool."""
import httpx
import json
import sys

MCP_URL = "https://anima.lugia19.com/mcp/C7A9DzmXE-noTNnhvJpl0dvJ0PdYv2aNKjKSJKvALTs"

#MCP_URL = "https://jun-visited-beta-blacks.trycloudflare.com/mcp"
HEADERS = {"Accept": "application/json, text/event-stream"}

prompt = sys.argv[1] if len(sys.argv) > 1 else "masterpiece, best quality, @cutesexyrobutts, 1girl, a girl standing in a field of flowers"

# Step 1: Initialize
resp = httpx.post(MCP_URL, headers=HEADERS, follow_redirects=True, timeout=30, json={
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0.0"},
    },
})
print("Init:", resp.status_code)

# Step 2: Call the tool
resp = httpx.post(MCP_URL, headers=HEADERS, follow_redirects=True, json={
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": "generate_image",
        "arguments": {"prompt": prompt},
    },
}, timeout=600)

result = resp.json()
print(json.dumps(result, indent=2)[:500])

# Save image if present
for item in result.get("result", {}).get("content", []):
    if item.get("type") == "image":
        import base64
        with open("output.jpg", "wb") as f:
            f.write(base64.b64decode(item["data"]))
        print("Saved to output.jpg")
