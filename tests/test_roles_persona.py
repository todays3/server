"""Assistant persona voice in curation prompts."""

from __future__ import annotations

from app.services.roles import assistant_persona, parse_role_settings, parse_roles, roles_profile_brief


def test_roles_profile_brief_includes_persona_voice():
    brief = roles_profile_brief(
        ["developer", "investor"],
        parse_role_settings('{"assistant_names":{"developer":"민준"}}'),
    )
    assert "시니어 소프트웨어 개발자" in brief
    assert "말투:" in brief
    assert "개미 투자자" in brief
    assert "번갈아" not in brief
    assert "각 어시스턴트마다 소식 3개" in brief


def test_assistant_persona_has_all_roles():
    for role in (
        "investor",
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
    assert "극예술 큐레이터" in brief
    assert "뮤지컬" in brief
    assert "연극" in brief
    assert "3개" in brief
    assert "극예술" in brief


def test_legacy_theater_role_and_genres_migrate():
    roles = parse_roles("theater")
    assert roles == ["performing_arts"]
    brief = roles_profile_brief(
        roles,
        parse_role_settings('{"theater_genres":["뮤지컬"],"assistant_names":{"theater":"예린"}}'),
    )
    assert "극예술" in brief
    assert "뮤지컬" in brief
