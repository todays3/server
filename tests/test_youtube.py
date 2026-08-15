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

    class PlaylistResp:
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
                    },
                    {
                        "snippet": {
                            "title": "조회수 적은 영상",
                            "description": "스킵",
                            "resourceId": {"videoId": "lowviews000"},
                        }
                    },
                ]
            }

    class VideosResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "items": [
                    {"id": "abc123xyz00", "statistics": {"viewCount": "25000"}},
                    {"id": "lowviews000", "statistics": {"viewCount": "120"}},
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
            assert params["key"] == "test-key"
            if "playlistItems" in url:
                assert params["playlistId"].startswith("UU")
                return PlaylistResp()
            assert "videos" in url
            return VideosResp()

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
            if "search" in getattr(self, "_url", ""):
                return {
                    "items": [
                        {
                            "id": {"videoId": "vid99"},
                            "snippet": {
                                "title": "연애 조언",
                                "description": "거리감",
                                "channelTitle": "지식인사이드",
                            },
                        },
                        {
                            "id": {"videoId": "tiny01"},
                            "snippet": {
                                "title": "초소형 조회",
                                "description": "x",
                                "channelTitle": "지식인사이드",
                            },
                        },
                    ]
                }
            return {
                "items": [
                    {"id": "vid99", "statistics": {"viewCount": "10000"}},
                    {"id": "tiny01", "statistics": {"viewCount": "9999"}},
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
            if "search" in url:
                assert params["q"] == "라이프"
                resp = Resp()
                resp._url = url
                return resp
            resp = Resp()
            resp._url = url
            return resp

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


def test_views_from_feed_entry_reads_media_statistics():
    class Entry:
        media_statistics = {"views": "15000"}

    assert yt.views_from_feed_entry(Entry()) == 15000
    assert yt.meets_view_floor(15000) is True
    assert yt.meets_view_floor(9999) is False
    assert yt.meets_view_floor(None) is False


def test_channels_for_semiconductor_does_not_force_finance_channels():
    rows = yt.channels_for(["반도체/기술동향/HBM·메모리"], [])
    ids = [cid for _, cid in rows]
    assert "UChlgI3UHCOnwUGzWzbJEuYw" not in ids
