"""Assistant persona voice in curation prompts."""

from __future__ import annotations

from app.services.roles import assistant_persona, parse_role_settings, roles_profile_brief


def test_roles_profile_brief_includes_persona_voice():
    brief = roles_profile_brief(
        ["developer", "investor"],
        parse_role_settings('{"assistant_names":{"developer":"민준"}}'),
    )
    assert "시니어 소프트웨어 개발자" in brief
    assert "말투:" in brief
    assert "개미 투자자" in brief
    assert "번갈아 반영" in brief


def test_assistant_persona_has_all_roles():
    for role in ("investor", "developer", "doctor", "semiconductor", "job_seeker"):
        persona = assistant_persona(role)
        assert persona["archetype"]
        assert persona["voice"]
