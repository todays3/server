"""Assistant persona voice in curation prompts."""

from __future__ import annotations

from app.services.roles import assistant_persona, parse_role_settings, parse_roles, roles_profile_brief


def test_roles_profile_brief_includes_persona_voice():
    brief = roles_profile_brief(
        ["developer", "investor"],
        parse_role_settings('{"assistant_names":{"developer":"민준"}}'),
    )
    assert "senior software" in brief.lower()
    assert "Voice:" in brief
    assert "latest tech" in brief.lower() or "tech trend" in brief.lower()
    assert "verified" in brief.lower()
    assert "hiring" in brief.lower() or "move" in brief.lower()
    assert "retail investor" in brief.lower()
    assert "번갈아" not in brief
    assert "separately" in brief.lower()
    assert "1 listed stock" in brief or "one listed stock" in brief.lower()


def test_developer_brief_puts_latest_trend_first():
    brief = roles_profile_brief(
        ["developer"],
        parse_role_settings('{"assistant_names":{"developer":"민준"}}'),
    )
    assert "Priority" in brief or "priority" in brief.lower()
    assert "evergreen" in brief.lower()
    assert "인물" in brief
    assert "흐름" in brief


def test_assistant_persona_has_all_roles():
    for role in (
        "investor",
        "stock_analyst",
        "developer",
        "doctor",
        "semiconductor",
        "job_seeker",
        "music",
        "reader",
        "movie",
        "otaku",
        "gaming",
        "performing_arts",
    ):
        persona = assistant_persona(role)
        assert persona["archetype"]
        assert persona["voice"]


def test_performing_arts_brief_asks_for_three_show_urls():
    brief = roles_profile_brief(
        ["performing_arts"],
        parse_role_settings(
            '{"performing_arts_genres":["뮤지컬","연극"],"assistant_names":{"performing_arts":"예린"}}'
        ),
    )
    assert "performing-arts" in brief.lower() or "performing arts" in brief.lower()
    assert "뮤지컬" in brief
    assert "연극" in brief
    assert "3" in brief


def test_legacy_theater_role_and_genres_migrate():
    roles = parse_roles("theater")
    assert roles == ["performing_arts"]
    brief = roles_profile_brief(
        roles,
        parse_role_settings('{"theater_genres":["뮤지컬"],"assistant_names":{"theater":"예린"}}'),
    )
    assert "극예술" in brief or "performing" in brief.lower()
    assert "뮤지컬" in brief


def test_stock_analyst_brief_asks_for_one_stock_not_three_articles():
    brief = roles_profile_brief(
        ["stock_analyst"],
        parse_role_settings(
            '{"investor_market":"국내증시","investor_themes":["반도체"],"assistant_names":{"stock_analyst":"도윤"}}'
        ),
    )
    assert "stock analyst" in brief.lower()
    assert "도윤" in brief
    assert "1 listed stock" in brief or "one listed stock" in brief.lower()
    assert "Do not pick 3 articles" in brief
    assert "not a buy" in brief.lower()
