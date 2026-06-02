import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from agent.search_providers import SearchProviderManager

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_tavily_search_success(mock_post):
    # Mock Tavily response as a MagicMock since raise_for_status and json are sync
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {"title": "Tavily Title", "url": "https://tavily.com/1", "content": "Tavily snippet content."}
        ]
    }
    mock_post.return_value = mock_resp

    manager = SearchProviderManager()
    prov = {
        "name": "test_tavily",
        "type": "tavily",
        "url": "https://api.tavily.com/search",
        "api_key_vault_key": "tavily_api_key",
    }
    
    with patch.object(manager, "_get_api_key", return_value="dummy_key"):
        text, count, remote_stats = await manager._call_provider(prov, "test query")
        
    assert count == 1
    assert "Tavily Title" in text
    assert "https://tavily.com/1" in text
    assert "Tavily snippet content." in text
    mock_post.assert_called_once()
    assert mock_post.call_args[1]["json"]["query"] == "test query"


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_google_search_success(mock_get):
    # Mock Google Custom Search response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "items": [
            {"title": "Google Title", "link": "https://google.com/1", "snippet": "Google search snippet."}
        ]
    }
    mock_get.return_value = mock_resp

    manager = SearchProviderManager()
    prov = {
        "name": "test_google",
        "type": "google",
        "url": "https://www.googleapis.com/customsearch/v1?cx=my-engine-id",
        "api_key_vault_key": "google_api_key",
    }
    
    with patch.object(manager, "_get_api_key", return_value="dummy_key"):
        text, count, remote_stats = await manager._call_provider(prov, "test query")
        
    assert count == 1
    assert "Google Title" in text
    assert "https://google.com/1" in text
    assert "Google search snippet." in text
    mock_get.assert_called_once()
    assert mock_get.call_args[1]["params"]["cx"] == "my-engine-id"
    assert mock_get.call_args[1]["params"]["key"] == "dummy_key"


@pytest.mark.asyncio
async def test_google_search_missing_cx():
    manager = SearchProviderManager()
    prov = {
        "name": "test_google",
        "type": "google",
        "url": "https://www.googleapis.com/customsearch/v1",  # No cx parameter!
        "api_key_vault_key": "google_api_key",
    }
    
    with patch.object(manager, "_get_api_key", return_value="dummy_key"):
        with pytest.raises(RuntimeError, match="Google Custom Search ID"):
            await manager._call_provider(prov, "test query")


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_bing_search_success(mock_get):
    # Mock Bing response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "webPages": {
            "value": [
                {"name": "Bing Title", "url": "https://bing.com/1", "snippet": "Bing search snippet."}
            ]
        }
    }
    mock_get.return_value = mock_resp

    manager = SearchProviderManager()
    prov = {
        "name": "test_bing",
        "type": "bing",
        "url": "https://api.bingmicrosoft.com/v7.0/search",
        "api_key_vault_key": "bing_api_key",
    }
    
    with patch.object(manager, "_get_api_key", return_value="dummy_key"):
        text, count, remote_stats = await manager._call_provider(prov, "test query")
        
    assert count == 1
    assert "Bing Title" in text
    assert "https://bing.com/1" in text
    assert "Bing search snippet." in text
    mock_get.assert_called_once()
    assert mock_get.call_args[1]["headers"]["Ocp-Apim-Subscription-Key"] == "dummy_key"


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_wikipedia_search_success(mock_get):
    # Mock Wikipedia response
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "query": {
            "search": [
                {"title": "Python Programming", "snippet": "Python is a high-level language."}
            ]
        }
    }
    mock_get.return_value = mock_resp

    manager = SearchProviderManager()
    prov = {
        "name": "test_wiki",
        "type": "wikipedia",
        "url": "https://en.wikipedia.org/w/api.php",
    }
    
    text, count, remote_stats = await manager._call_provider(prov, "python")
        
    assert count == 1
    assert "Python Programming" in text
    assert "https://en.wikipedia.org/wiki/Python_Programming" in text
    assert "Python is a high-level language." in text
    mock_get.assert_called_once()
    assert mock_get.call_args[1]["params"]["srsearch"] == "python"
