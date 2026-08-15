"""Pydantic request validators."""

import pytest

from app.schemas import PreferenceUpdate, ProfileUpdate, RegisterRequest, SendTimeSlot


def test_register_rejects_whitespace_password():
    with pytest.raises(Exception):
        RegisterRequest(email="a@b.com", password="        ", display_name="n")


def test_preference_update_rejects_too_many_topics():
    with pytest.raises(Exception):
        PreferenceUpdate(topics=["t"] * 61)


def test_preference_update_rejects_too_many_sources():
    with pytest.raises(Exception):
        PreferenceUpdate(sources=["s"] * 41)


def test_preference_update_rejects_empty_send_times():
    with pytest.raises(Exception):
        PreferenceUpdate(send_times=[])


def test_preference_update_rejects_too_many_send_times():
    with pytest.raises(Exception):
        PreferenceUpdate(send_times=[SendTimeSlot(hour=1, minute=0)] * 6)


def test_preference_update_empty_keeps_optional_none():
    empty = PreferenceUpdate()
    assert empty.topics is None


def test_profile_update_rejects_future_birth_date():
    with pytest.raises(Exception):
        ProfileUpdate(display_name="닉", occupation="의사", birth_date="2999-01-01")


def test_profile_update_accepts_empty_occupation_and_no_birth():
    payload = ProfileUpdate(display_name="닉", occupation="", birth_date=None)
    assert payload.display_name == "닉"
    assert payload.occupation == ""
    assert payload.birth_date is None
