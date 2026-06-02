import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from api.mcp import _scan_port, scan_local_mcp_ports

@pytest.mark.asyncio
@patch("asyncio.open_connection", new_callable=AsyncMock)
@patch("httpx.AsyncClient.get")
async def test_scan_port_open_http_success(mock_get, mock_open_connection):
    # Mock connection check success
    mock_writer = AsyncMock()
    mock_writer.close = MagicMock()
    mock_open_connection.return_value = (AsyncMock(), mock_writer)
    
    # Mock HTTP response
    mock_resp = MagicMock()
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"name": "Test MCP Server"}
    mock_get.return_value = mock_resp
    
    res = await _scan_port(8123)
    assert res is not None
    assert res["port"] == 8123
    assert res["status"] == "open"
    assert res["name"] == "Test MCP Server"

@pytest.mark.asyncio
@patch("asyncio.open_connection", new_callable=AsyncMock)
async def test_scan_port_closed(mock_open_connection):
    # Mock connection timeout / failure
    mock_open_connection.side_effect = Exception("Connection refused")
    
    res = await _scan_port(8123)
    assert res is None
