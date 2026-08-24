"""Kakao memo chunking + digest item attachment tests."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Preference
from app.services.digest import TEST_DATA_NOTICE, _attach_insights, _format_body, build_test_digest_preview
from app.services.kakao import MEMO_TEXT_LIMIT, split_memo_chunks


def test_split_memo_chunks_single_when_short():
    chunks = split_memo_chunks("제목", "짧은 본문")
    assert len(chunks) == 1
    assert "제목" in chunks[0]
    assert "짧은 본문" in chunks[0]


def test_split_memo_chunks_separates_assistants_before_length():
    body = "민준요. 좋은 저녁이에요, 민수님.\n오늘 10개 중에 고른 3개입니다.\n\n\x1e\n\n하람요. 좋은 저녁이에요, 민수님.\n오늘 10개 중에 고른 3개입니다."
    chunks = split_memo_chunks("하루만장", body)
    assert len(chunks) == 2
    assert "민준" in chunks[0] and "하람" not in chunks[0]
    assert "하람" in chunks[1] and "민준" not in chunks[1]
    assert "\x1e" not in chunks[0] and "\x1e" not in chunks[1]


def test_split_memo_chunks_respects_limit():
    body = "\n".join([f"{i}) 항목 내용 " + ("가" * 80) for i in range(1, 20)])
    chunks = split_memo_chunks("하루만장", body)
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
            "blurb": "연준 발언입니다. 기술주도 흔들렸습니다.",
            "url": "https://a.example",
            "insight_q": "금리가 주가에 미치는 영향이 궁금해요",
            "insight_url": "https://b.example",
        }
    ]
    body = _format_body("테스트", items, pref, ["경제/주식"], reviewed_count=12, now=datetime(2026, 8, 16, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")))
    first = body.splitlines()[0]
    assert "하루만장 ·" not in first
    assert "테스트님" in first
    assert first.endswith("테스트님.") or first.endswith("테스트님?")
    assert "오늘 12개 중에 고른 1개입니다." in body
    assert "첫째. 📰 금리" in body
    assert "[아티클]" not in body
    assert "주제:" not in body
    assert "기술주도 흔들렸습니다" not in body
    assert "연준 발언입니다." in body
    assert "추가 질문:" in body
    assert "https://b.example" in body
    assert "12개 중 골랐습니다." not in body


def test_format_body_uses_assistant_name_when_roles_set():
    pref = Preference(
        insight_questions=False,
        roles="developer",
        role_settings='{"assistant_names":{"developer":"지훈"}}',
    )
    items = [{"kind": "아티클", "title": "AI", "blurb": "요약.", "url": "https://a.example"}]
    body = _format_body(
        "민수",
        items,
        pref,
        ["IT"],
        now=datetime(2026, 8, 16, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    assert "지훈" in body.splitlines()[0]
    assert "민수님" in body.splitlines()[0]


def test_format_body_puts_a_rule_between_articles():
    pref = Preference(insight_questions=False, notes="짧게")
    items = [
        {"kind": "아티클", "title": "금리", "blurb": "요약입니다.", "url": "https://a.example", "why": "피드 1위·3시간 전"},
        {"kind": "유튜브", "title": "시황", "blurb": "장 흐름입니다.", "url": "https://b.example", "why": "조회 2.4만·5시간 전"},
        {"kind": "커뮤니티", "title": "토론", "blurb": "수급입니다.", "url": "https://c.example"},
    ]
    body = _format_body("민수", items, pref, ["경제"])
    assert body.count("────────") == 2
    assert "첫째." in body and "둘째." in body and "셋째." in body
    assert "선정이유: 피드 1위·3시간 전" in body
    assert "요청: 짧게" in body
    assert "— 하루만장" not in body


def test_format_body_prefixes_flow_issue_person_angles():
    pref = Preference(insight_questions=False, notes="")
    items = [
        {"kind": "아티클", "title": "시황", "blurb": "동향입니다.", "url": "https://a.example", "angle": "흐름"},
        {"kind": "아티클", "title": "출시", "blurb": "업데이트입니다.", "url": "https://b.example", "angle": "이슈"},
        {"kind": "아티클", "title": "인터뷰", "blurb": "발언입니다.", "url": "https://c.example", "angle": "인물"},
    ]
    body = _format_body("민수", items, pref, ["IT"])
    assert "첫째. 📰 흐름 · 시황" in body
    assert "둘째. 📰 이슈 · 출시" in body
    assert "셋째. 📰 인물 · 인터뷰" in body


def test_format_body_keeps_a_brief_of_three_per_assistant():
    pref = Preference(insight_questions=False, notes="", roles="developer,music")
    items = [
        {"kind": "아티클", "title": "AI", "blurb": "개발 소식입니다.", "url": "https://a.example", "assistant": "민준"},
        {"kind": "유튜브", "title": "React", "blurb": "프레임워크입니다.", "url": "https://b.example", "assistant": "민준"},
        {"kind": "커뮤니티", "title": "HN", "blurb": "토론입니다.", "url": "https://c.example", "assistant": "민준"},
        {"kind": "아티클", "title": "차트", "blurb": "신곡입니다.", "url": "https://d.example", "assistant": "하람"},
        {"kind": "유튜브", "title": "MV", "blurb": "뮤직비디오입니다.", "url": "https://e.example", "assistant": "하람"},
        {"kind": "커뮤니티", "title": "멜론", "blurb": "음원입니다.", "url": "https://f.example", "assistant": "하람"},
    ]
    body = _format_body(
        "민수",
        items,
        pref,
        ["IT", "음악"],
        reviewed_count=40,
        now=datetime(2026, 8, 16, 18, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    assert body.count("고른 3개입니다.") == 2
    assert "\x1e" in body
    first, second = [part.strip() for part in body.split("\x1e") if part.strip()]
    assert "민준" in first and "하람" not in first
    assert "하람" in second and "민준" not in second
    assert first.splitlines()[0].endswith("민수님.") or first.splitlines()[0].endswith("민수님?")
    assert second.splitlines()[0].endswith("민수님.") or second.splitlines()[0].endswith("민수님?")
    assert body.count("첫째.") == 2
    assert "여섯째" not in body


def test_test_digest_marks_sample_and_uses_current_kakao_shape():
    from app.models import User

    user = User(email="a@example.com", display_name="민수", status="approved")
    pref = Preference(topics="경제", notes="", insight_questions=False, roles="")
    preview = build_test_digest_preview(user, pref)
    lines = preview.body.splitlines()
    assert TEST_DATA_NOTICE in preview.body
    assert lines[0].endswith("민수님.") or lines[0].endswith("민수님?")
    assert TEST_DATA_NOTICE in lines[2]
    assert "오늘 " in preview.body and "중에 고른 3개입니다." in preview.body
    assert "첫째." in preview.body
    assert "선정이유:" in preview.body
    assert "고정 큐레이션" not in preview.body
    assert "[아티클]" not in preview.body


def test_test_digest_groups_dummy_items_per_assistant():
    from app.models import User

    user = User(email="a@example.com", display_name="민수", status="approved")
    pref = Preference(
        topics="IT/개발/all,음악/장르/K-POP",
        notes="",
        insight_questions=False,
        roles="developer,music",
        role_settings='{"assistant_names":{"developer":"지훈","music":"하람"}}',
    )
    preview = build_test_digest_preview(user, pref)
    assert TEST_DATA_NOTICE in preview.body
    parts = [part.strip() for part in preview.body.split("\x1e") if part.strip()]
    assert len(parts) == 2
    assert preview.body.count("첫째.") == 2
    assert "지훈" in parts[0]
    assert "하람" in parts[1]
    assert preview.body.count("고른 3개입니다.") == 2
    assert all(TEST_DATA_NOTICE in part for part in parts)


