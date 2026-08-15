"""Kakao memo chunking + digest item attachment tests."""

from app.services.kakao import MEMO_TEXT_LIMIT, split_memo_chunks
from app.services.digest import _attach_insights, _format_body
from app.models import Preference


def test_split_memo_chunks_single_when_short():
    chunks = split_memo_chunks("제목", "짧은 본문")
    assert len(chunks) == 1
    assert "제목" in chunks[0]
    assert "짧은 본문" in chunks[0]


def test_split_memo_chunks_respects_limit():
    body = "\n".join([f"{i}) 항목 내용 " + ("가" * 80) for i in range(1, 20)])
    chunks = split_memo_chunks("오늘의 3", body)
    assert len(chunks) >= 2
    assert all(len(c) <= MEMO_TEXT_LIMIT for c in chunks)


def test_attach_insights_one_per_item():
    pref = Preference(insight_questions=True)
    items = [
        {"kind": "아티클", "title": "금리 발언 요약", "blurb": "연준 금리", "url": "https://a.example"},
        {"kind": "유튜브", "title": "시황", "blurb": "장 흐름", "url": "https://b.example"},
        {"kind": "커뮤니티", "title": "토론", "blurb": "수급", "url": "https://c.example"},
    ]
    out = _attach_insights(items, pref=pref, candidates=[])
    assert len(out) == 3
    for row in out:
        assert row["insight_q"]
        assert row["insight_url"].startswith("http")


def test_format_body_includes_insight_when_enabled():
    pref = Preference(insight_questions=True, notes="")
    items = [
        {
            "kind": "아티클",
            "title": "금리",
            "blurb": "요약",
            "url": "https://a.example",
            "insight_q": "금리가 주가에 미치는 영향이 궁금해요",
            "insight_url": "https://b.example",
        }
    ]
    body = _format_body("테스트", items, pref, ["경제/주식"], reviewed_count=12)
    assert "✨ 인사이트:" in body
    assert "https://b.example" in body
    assert "📰" in body
    assert body.splitlines()[0] == "12개의 아티클을 종합 검수했습니다"
