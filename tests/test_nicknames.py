from random import Random

from app.services.nicknames import ANIMALS, DOINGS, random_nickname, split_nickname


def test_random_nickname_is_doing_plus_animal():
    name = random_nickname(Random(7))
    doing, animal = split_nickname(name)
    assert doing in DOINGS
    assert animal in ANIMALS


def test_random_nickname_varies_with_seed():
    a = random_nickname(Random(1))
    b = random_nickname(Random(2))
    assert a != b
