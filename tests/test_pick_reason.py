from datetime import date

from app.catalog.ref_sites import all_site_ids
from app.models import Preference, User
from app.services.digest import _format_body, _heuristic_pick, personalize_candidates
from app.services.pick_reason import KIND_WHY, SITE_WHY, compose_pick_reason
from app.services.sources import SourceItem


def test_every_catalog_site_has_a_pick_reason():
    missing = [sid for sid in all_site_ids() if sid not in SITE_WHY]
    assert missing == []


def test_compose_pick_reason_uses_site_then_kind():
    hn = SourceItem(kind="커뮤니티", title="t", url="https://news.ycombinator.com/1", summary="", source="HN", site_id="hn")
    assert compose_pick_reason(hn) == "HN 프론트페이지"
    yt = SourceItem(kind="유튜브", title="t", url="https://youtu.be/x", summary="", source="yt")
    assert "1만" in compose_pick_reason(yt)
    job = compose_pick_reason(hn, job_match=True)
    assert "직무" in job
    assert len(job) <= 36


def test_format_body_includes_short_why_line():
    pref = Preference(insight_questions=False, notes="")
    items = [
        {
            "kind": "아티클",
            "title": "금리",
            "blurb": "요약",
            "url": "https://a.example",
            "why": "HN 프론트페이지",
        }
    ]
    body = _format_body("테스트", items, pref, ["경제"])
    assert "선정이유: HN 프론트페이지" in body


def test_personalize_attaches_pick_reason_and_job_flag():
    user = User(email="p@example.com", display_name="민수", occupation="반도체 연구원", birth_date=date(1990, 1, 1))
    items = [
        SourceItem(
            kind="아티클",
            title="HBM 반도체",
            url="https://d2.naver.com/1",
            summary="공정",
            source="D2",
            site_id="naver-d2",
        )
    ]
    ranked = personalize_candidates(items, user)
    assert "D2" in ranked[0].pick_reason
    assert "직무" in ranked[0].pick_reason


def test_heuristic_pick_copies_why():
    picked = _heuristic_pick(
        [
            SourceItem(
                kind="아티클",
                title="A",
                url="https://a.example",
                summary="s",
                source="s",
                site_id="hn",
                pick_reason="HN 프론트페이지",
            ),
            SourceItem(
                kind="유튜브",
                title="B",
                url="https://b.example",
                summary="s",
                source="s",
                pick_reason="조회수 1만+ 영상",
            ),
            SourceItem(
                kind="커뮤니티",
                title="C",
                url="https://c.example",
                summary="s",
                source="s",
                pick_reason="커뮤니티 인기글",
            ),
        ],
        "seed",
    )
    assert picked[0]["why"] == "HN 프론트페이지"


def test_kind_fallbacks_exist():
    assert set(KIND_WHY) >= {"유튜브", "아티클", "커뮤니티"}
