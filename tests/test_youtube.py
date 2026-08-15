"""YouTube Data API v3 collector (youtube-mcp compatible)."""

from app.config import get_settings
from app.services import youtube as yt


def test_uploads_playlist_id_from_uc():
    assert yt.uploads_playlist_id("UChlgI3UHCOnwUGzWzbJEuYw") == "UUhlgI3UHCOnwUGzWzbJEuYw"


def test_fetch_channel_videos_empty_without_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert yt.fetch_channel_videos("UChlgI3UHCOnwUGzWzbJEuYw", "삼프로TV") == []
    finally:
        get_settings.cache_clear()


def test_fetch_channel_videos_maps_watch_urls(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    get_settings.cache_clear()

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "snippet": {
                            "title": "오늘 시황",
                            "description": "금리와 환율",
                            "resourceId": {"videoId": "abc123xyz00"},
                        }
                    }
                ]
            }

    class Client:
        def __init__(self, **_k):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def get(self, url, params=None):
            assert "playlistItems" in url
            assert params["key"] == "test-key"
            assert params["playlistId"].startswith("UU")
            return Resp()

    monkeypatch.setattr("app.services.youtube.httpx.Client", Client)
    try:
        items = yt.fetch_channel_videos("UChlgI3UHCOnwUGzWzbJEuYw", "삼프로TV")
        assert len(items) == 1
        assert items[0].kind == "유튜브"
        assert items[0].url == "https://www.youtube.com/watch?v=abc123xyz00"
        assert "시황" in items[0].title
    finally:
        get_settings.cache_clear()


def test_search_videos_maps_results(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    get_settings.cache_clear()

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {
                        "id": {"videoId": "vid99"},
                        "snippet": {
                            "title": "연애 조언",
                            "description": "거리감",
                            "channelTitle": "지식인사이드",
                        },
                    }
                ]
            }

    class Client:
        def __init__(self, **_k):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def get(self, url, params=None):
            assert "search" in url
            assert params["q"] == "라이프"
            return Resp()

    monkeypatch.setattr("app.services.youtube.httpx.Client", Client)
    try:
        items = yt.search_videos("라이프")
        assert items[0].url.endswith("vid99")
        assert items[0].source == "지식인사이드"
    finally:
        get_settings.cache_clear()


def test_probe_youtube_site_skips_without_key(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert yt.probe_youtube_site("youtube-life") == []
    finally:
        get_settings.cache_clear()


def test_channels_for_prefers_site_then_topics():
    rows = yt.channels_for(["경제/주식/국내증시"], ["sampro"])
    ids = [cid for _, cid in rows]
    assert ids[0] == "UChlgI3UHCOnwUGzWzbJEuYw"
    assert "UChlgI3UHCOnwUGzWzbJEuYw" in ids
