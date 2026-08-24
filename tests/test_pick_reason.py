from datetime import datetime, timedelta, timezone

from app.catalog.ref_sites import all_site_ids
from app.models import Preference, User
from app.services.digest import _format_body, _heuristic_pick, personalize_candidates
from app.services.pick_reason import (
    KIND_WHY,
    age_label,
    clip_why,
    compose_pick_reason,
    engagement_from_text,
    format_compact_int,
    views_from_text,
)
from app.services.sources import SourceItem


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def test_format_compact_int_uses_korean_units():
    assert format_compact_int(25000) == "2.5만"
    assert format_compact_int(214) == "214"
    assert format_compact_int(2_500_000) == "250만"


def test_engagement_from_text_reads_hn_points_and_comments():
    blob = "<p>Points: 214</p><p># Comments: 87</p>"
    points, comments = engagement_from_text("title", blob)
    assert points == 214
    assert comments == 87


def test_engagement_from_text_reads_reddit_comment_count():
    points, comments = engagement_from_text("A trade idea (42 comments)")
    assert comments == 42
    assert points is None


def test_views_from_text_reads_korean_and_english_counts():
    assert views_from_text("조회수 24,000회 넘는 영상") == 24_000
    assert views_from_text("clips with 15000 views today") == 15_000


def test_age_label_is_hours_or_days():
    assert age_label(NOW - timedelta(hours=3), now=NOW) == "3시간 전"
    assert age_label(NOW - timedelta(minutes=20), now=NOW) == "1시간 내"
    assert age_label(NOW - timedelta(hours=30), now=NOW) == "1일 전"


def test_compose_pick_reason_prefers_article_numbers():
    item = SourceItem(
        kind="커뮤니티",
        title="t",
        url="https://news.ycombinator.com/1",
        summary="Points: 214",
        source="HN",
        site_id="hn",
        points=214,
        comments=87,
        list_rank=1,
        published_at=NOW - timedelta(hours=3),
    )
    why = compose_pick_reason(item, now=NOW)
    assert "점수 214" in why
    assert "댓글 87" in why or "3시간 전" in why or "피드 1위" in why
    assert "프론트페이지" not in why
    assert len(why) <= 48


def test_compose_pick_reason_uses_views_for_youtube():
    item = SourceItem(
        kind="유튜브",
        title="시황",
        url="https://youtu.be/x",
        summary="s",
        source="삼프로",
        views=24_000,
        published_at=NOW - timedelta(hours=5),
    )
    why = compose_pick_reason(item, now=NOW)
    assert "조회 2.4만" in why
    assert "5시간 전" in why


def test_compose_pick_reason_falls_back_to_dau_with_a_digit():
    item = SourceItem(
        kind="아티클",
        title="t",
        url="https://d2.naver.com/1",
        summary="공정",
        source="D2",
        site_id="naver-d2",
        job_hits=1,
        topic_hits=2,
    )
    why = compose_pick_reason(item)
    assert any(ch.isdigit() for ch in why)
    assert "직무" in why or "주제" in why


def test_every_catalog_site_gets_a_numeric_reason():
    missing = []
    for sid in all_site_ids():
        item = SourceItem(kind="아티클", title="제목입니다", url="https://x.example", summary="", source="s", site_id=sid)
        why = compose_pick_reason(item)
        if not why or not any(ch.isdigit() for ch in why):
            missing.append(sid)
    assert missing == []


def test_format_body_includes_short_why_line():
    pref = Preference(insight_questions=False, notes="")
    items = [
        {
            "kind": "아티클",
            "title": "금리",
            "blurb": "요약",
            "url": "https://a.example",
            "why": "점수 214·3시간 전",
        }
    ]
    body = _format_body("테스트", items, pref, ["경제"])
    assert "선정이유: 점수 214·3시간 전" in body


def test_personalize_attaches_numeric_pick_reason_and_job_hits():
    user = User(email="p@example.com", display_name="민수", occupation="반도체 연구원", birth_date=None)
    items = [
        SourceItem(
            kind="아티클",
            title="HBM 반도체",
            url="https://d2.naver.com/1",
            summary="공정",
            source="D2",
            site_id="naver-d2",
            points=40,
            list_rank=2,
        )
    ]
    ranked = personalize_candidates(items, user, Preference(topics="반도체"))
    assert ranked[0].job_hits >= 1
    assert any(ch.isdigit() for ch in ranked[0].pick_reason)
    assert "직무" in ranked[0].pick_reason
    assert "40" in ranked[0].pick_reason


def test_heuristic_pick_composes_numeric_why():
    picked = _heuristic_pick(
        [
            SourceItem(
                kind="아티클",
                title="A",
                url="https://a.example",
                summary="s",
                source="s",
                site_id="hn",
                points=214,
                list_rank=1,
            ),
            SourceItem(
                kind="유튜브",
                title="B",
                url="https://b.example",
                summary="s",
                source="s",
                views=12_000,
            ),
            SourceItem(
                kind="커뮤니티",
                title="C",
                url="https://c.example",
                summary="s",
                source="s",
                comments=30,
                list_rank=3,
            ),
        ],
        "seed",
    )
    assert "214" in picked[0]["why"]
    assert "조회" in picked[1]["why"]


def test_kind_fallbacks_exist():
    assert set(KIND_WHY) >= {"유튜브", "아티클", "커뮤니티"}
    assert all(any(ch.isdigit() for ch in text) for text in KIND_WHY.values())


def test_clip_why_caps_length():
    assert len(clip_why("x" * 80)) == 48
