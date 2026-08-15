"""Pydantic request validators."""

import pytest

from app.schemas import PreferenceUpdate, RegisterRequest, SendTimeSlot


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
