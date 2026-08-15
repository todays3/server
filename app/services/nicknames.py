"""Wholesome random nicknames for first-time signup: '{doing} {animal}'."""

from __future__ import annotations

import secrets
from random import Random

DOINGS: tuple[str, ...] = (
    "낮잠 자는",
    "커피 마시는",
    "산책하는",
    "책 읽는",
    "노래하는",
    "춤추는",
    "생각하는",
    "웃는",
    "별 보는",
    "비 듣는",
    "그림 그리는",
    "빵 굽는",
    "차 마시는",
    "구름 보는",
    "하품하는",
)

ANIMALS: tuple[str, ...] = (
    "판다",
    "고라니",
    "고양이",
    "수달",
    "펭귄",
    "여우",
    "너구리",
    "토끼",
    "햄스터",
    "돌고래",
    "쿼카",
    "알파카",
    "북극곰",
    "미어캣",
    "코알라",
    "다람쥐",
    "오리",
    "부엉이",
    "참새",
    "고래",
)


def random_nickname(rng: Random | secrets.SystemRandom | None = None) -> str:
    pick = rng.choice if rng is not None else secrets.choice
    return f"{pick(DOINGS)} {pick(ANIMALS)}"


def split_nickname(name: str) -> tuple[str, str]:
    doing, _, animal = name.rpartition(" ")
    return doing, animal
