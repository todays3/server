"""One-time Kakao login tickets."""

from app.services.oauth_tickets import consume_login_ticket, issue_login_ticket


def test_oauth_ticket_unknown_is_none():
    assert consume_login_ticket("missing") is None


def test_oauth_ticket_expires(monkeypatch):
    ticket = issue_login_ticket("tok")
    monkeypatch.setattr("app.services.oauth_tickets.time.monotonic", lambda: 10**12)
    assert consume_login_ticket(ticket) is None
