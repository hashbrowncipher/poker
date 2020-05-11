from enum import IntEnum
from dataclasses import dataclass
from typing import List


class Value(IntEnum):
    CARD = 1
    PAIR = 2
    TWO_PAIRS = 3
    SET = 4
    STRAIGHT = 5
    FLUSH = 6
    FULL_HOUSE = 7
    QUAD = 8
    STRAIGHT_FLUSH = 9


class NotACard(Exception):
    pass


class Card(str):
    def __init__(self, value):
        if len(self) != 2:
            raise NotACard

    @property
    def value(self):
        # Alternatively we could encode cards in descending order across ASCII
        code = self[0]
        if code == 'A':
            return 12
        if code == 'K':
            return 11
        if code == 'Q':
            return 10
        if code == 'J':
            return 9
        if code == 'X':
            return 8
        return ord(code) - 50

    @property
    def suit(self):
        return self[1]

    def __lt__(self, other):
        return self.value < other.value


@dataclass
class Hand:
    value: Value
    cards: str

    def __init__(self, value, cards):
        self.value = value
        self.cards = ''.join(cards)

        if isinstance(cards, str):
            cards = [Card(cards[i:i+2]) for i in range(0, len(cards), 2)]

        self.sort_key = (self.value, [c.value for c in cards])

        if len(self.cards) != 10:
            raise ValueError("Hand has wrong length", self.cards)


def _get_value_groups(cards):
    ret = [[] for _ in range(13)]
    for card in cards:
        ret[card.value].append(card)

    return ret


def get_flushes(value_groups: List[Card]):
    suits = dict(S=[], H=[], C=[], D=[])
    for group in value_groups[::-1]:
        for card in group:
            suits[card.suit].append(card)

    for suit, suit_cards in suits.items():
        if len(suit_cards) < 5:
            continue

        value_groups = _get_value_groups(suit_cards)
        straight = get_straight(value_groups)
        if straight:
            return Hand(Value.STRAIGHT_FLUSH, straight.cards)

        # With 7 cards available, the presence of a flush precludes the presence of
        # the (higher valued) full house or four of a kind.
        return Hand(Value.FLUSH, suit_cards[0:5])


def get_straight(value_groups: List[Card]):
    for i in range(12, 2, -1):
        bounds = range(i, i-5, -1)
        if not all(value_groups[j] for j in bounds):
            continue

        ret_cards = [value_groups[j][0] for j in bounds]
        return Hand(Value.STRAIGHT, ret_cards)


def get_matched_values(grouped) -> List[Card]:
    lengths: List[List[Card]] = [[] for _ in range(4)]

    for group in grouped[::-1]:
        lengths[len(group) - 1].extend(group)

    # A quad
    if lengths[3]:
        value = lengths[3][0].value
        for group in grouped:
            if not group or group[0].value == value:
                continue

            kicker = group[0]

        return Hand(Value.QUAD, lengths[3] + [kicker])

    # Two sets
    if len(lengths[2]) > 3:
        return Hand(Value.FULL_HOUSE, lengths[2][0:5])

    # A typical full house
    if lengths[2] and lengths[1]:
        return Hand(Value.FULL_HOUSE, lengths[2] + lengths[1][0:2])

    if lengths[2]:
        return Hand(Value.SET, lengths[2] + lengths[0][0:2])

    # Three pairs. Only two count
    if len(lengths[1]) > 4:
        kicker = max(lengths[1][4], lengths[0][0])
        return Hand(Value.TWO_PAIRS, lengths[1][0:4] + [kicker])

    # Two pairs
    if len(lengths[1]) > 2:
        return Hand(Value.TWO_PAIRS, lengths[1][0:4] + [lengths[0][0]])

    # A pair and three kickers
    if lengths[1]:
        return Hand(Value.PAIR, lengths[1] + lengths[0][0:3])

    # A high card and four kickers
    return Hand(Value.CARD, lengths[0][0:5])


def _find_best_hand(cards):
    grouped = _get_value_groups(cards)

    hand = get_flushes(grouped)
    if hand:
        return hand

    matched_values = get_matched_values(grouped)
    if matched_values.value in (Value.QUAD, Value.FULL_HOUSE):
        return matched_values

    hand = get_straight(grouped)
    if hand is not None:
        return hand

    return matched_values


def find_best_hand(card_str):
    cards = [Card(card_str[i:i+2]) for i in range(0, len(card_str), 2)]
    return _find_best_hand(cards)


def _get_winners(hands):
    evaluated_hands = [(owner, find_best_hand(h)) for (owner, h) in hands]

    it = iter(sorted(evaluated_hands, key=lambda hand: hand[1].sort_key, reverse=True))
    first = next(it)
    yield first
    argmax = first[1].sort_key
    for value in it:
        if value[1].sort_key != argmax:
            break
        yield value


def get_winners(hands):
    return list(_get_winners(hands))
