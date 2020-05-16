from poker.hands import Card
from poker.hands import Hand
from poker.hands import Value
from poker.hands import find_best_hand
from poker.hands import get_winners
from itertools import count


def _split_hand(value, cards):
    return Hand(value, [Card(cards[i : i + 2]) for i in range(0, len(cards), 2)])


def test_four():
    assert find_best_hand("AHQHQDQSQC3H2C") == Hand(Value.QUAD, "QHQDQSQCAH")

    # Kicker is embedded in a pair
    assert find_best_hand("QHQDQSQC3H3D8C") == Hand(Value.QUAD, "QHQDQSQC8C")

    # Kicker is embedded in a pair
    assert find_best_hand("QHQDQSQC3H3D2C") == Hand(Value.QUAD, "QHQDQSQC3H")

    # Kicker is embedded in a set
    assert find_best_hand("QHQDQSQC3H3D3C") == Hand(Value.QUAD, "QHQDQSQC3H")


def test_set():
    assert find_best_hand("QDJCXH4CAHASAD") == Hand(Value.SET, "AHASADQDJC")


def test_full_house():
    assert find_best_hand("AHKHQHQDQC4H4C") == Hand(Value.FULL_HOUSE, "QHQDQC4H4C")
    assert find_best_hand("AHASQHQDQC4H4C") == Hand(Value.FULL_HOUSE, "QHQDQCAHAS")

    # Two sets on the board
    assert find_best_hand("QDQCQH4CAHASAD") == Hand(Value.FULL_HOUSE, "AHASADQDQC")


def test_straight_flush():
    assert find_best_hand("AHKHQHJHXH9H8H") == Hand(Value.STRAIGHT_FLUSH, "AHKHQHJHXH")
    assert find_best_hand("ASKHQHJHXH9H8H") == Hand(Value.STRAIGHT_FLUSH, "KHQHJHXH9H")
    assert find_best_hand("AHQHJHXH9H8H7H") == Hand(Value.STRAIGHT_FLUSH, "QHJHXH9H8H")
    assert find_best_hand("AHQHJHXH9H8H7H") == Hand(Value.STRAIGHT_FLUSH, "QHJHXH9H8H")

    # Ace plays low
    assert find_best_hand("KHQH5H4H3H2HAH") == Hand(Value.STRAIGHT_FLUSH, "5H4H3H2HAH")


def test_straight():
    assert find_best_hand("AHJD6S5S4H3C2D") == Hand(Value.STRAIGHT, "6S5S4H3C2D")

    # Embedded pair
    assert find_best_hand("AHKHQHJDXD5C5S") == Hand(Value.STRAIGHT, "AHKHQHJDXD")

    # Ace plays low
    assert find_best_hand("KHQH5H4S3H2DAC") == Hand(Value.STRAIGHT, "5H4S3H2DAC")

    # Ace plays low, with included values
    assert find_best_hand("5H5S4S4H3H2DAC") == Hand(Value.STRAIGHT, "5H4S3H2DAC")


def test_flush():
    assert find_best_hand("ADQHJHXH9H7H6H") == Hand(Value.FLUSH, "QHJHXH9H7H")

    # Trips within the flush
    assert find_best_hand("QH5CXH9H5H5DAH") == Hand(Value.FLUSH, "AHQHXH9H5H")

    # Straight and flush, but not straight-flush
    assert find_best_hand("8H9HXDJHQHKHAH") == Hand(Value.FLUSH, "AHKHQHJH9H")


def test_two_pair():
    # The two-pair kicker is also embedded in a pair
    assert find_best_hand("ASAHKSKHQSQHJS") == Hand(Value.TWO_PAIRS, "ASAHKSKHQS")

    # There are three pairs, but the kicker is unpaired
    assert find_best_hand("ASAHKSKHJHJSQS") == Hand(Value.TWO_PAIRS, "ASAHKSKHQS")


def test_pair():
    assert find_best_hand("ASADKS9H5H4H3H") == Hand(Value.PAIR, "ASADKS9H5H")


def test_high_card():
    # Near straight
    assert find_best_hand("JDQDXS5C4D9H2S") == Hand(Value.CARD, "QDJDXS9H5C")


def test_comparison():
    board = "ASJSXH8H5H"

    hands = [
        board + "KDQC",
        board + "AHAD",
        board + "KCQD",
        board + "KS6H",
        board + "QS9C",
    ]

    expecteds = [
        Hand(Value.STRAIGHT, "ASKDQCJSXH"),
        Hand(Value.SET, "ASAHADJSXH"),
        Hand(Value.STRAIGHT, "ASKCQDJSXH"),
        Hand(Value.CARD, "ASKSJSXH8H"),
        Hand(Value.STRAIGHT, "QSJSXH9C8H"),
    ]

    for hand, expected in zip(hands, expecteds):
        assert find_best_hand(hand) == expected

    player_hands = zip(count(), hands)

    assert get_winners(player_hands) == [
        (0, Hand(Value.STRAIGHT, "ASKDQCJSXH")),
        (2, Hand(Value.STRAIGHT, "ASKCQDJSXH")),
    ]
