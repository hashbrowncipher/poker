import logging
from random import SystemRandom
from pydantic import BaseModel
from typing import Dict
from typing import Optional
from typing import List

from poker.consul import ConsulKey
from itertools import product
from poker.consul import NOT_PRESENT
from poker.hands import get_winners
from poker.consul import NO_CHANGE
from enum import IntEnum

logger = logging.getLogger(__name__)
random = SystemRandom()

# Deck management
_NUMBERS = 'AKQJX98765432'
_SUITS = 'SHCD'
_CARDS = ''.join([number + suit for number, suit in product(_NUMBERS, _SUITS)])


class Stage(IntEnum):
    PRE_FLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3


REVEALED_CARDS = {
    Stage.PRE_FLOP: 0,
    Stage.FLOP: 3,
    Stage.TURN: 4,
    Stage.RIVER: 5,
}


def get_card(index):
    index *= 2
    return _CARDS[index:index + 2]


class Player(BaseModel):
    name: str
    balance: int

    def decrement_balance(self, value):
        if self.balance >= value:
            self.balance -= value
            return value
        else:
            ret = self.balance
            return ret

    def increment_balance(self, value):
        self.balance += value


class PlayerInHand(BaseModel):
    session_id: str

    # this player's bet in the current round
    bet: int

    # how much are they eligible to take from the pot in this hand?
    # None indicates folded
    eligibility: Optional[int] = 0

    # Can the player bet in the current round?
    has_option: bool = True


class PlayersInHand(List[PlayerInHand]):
    def rotate(self):
        return self[1:] + [self[0]]


class Game(BaseModel):
    players: List[PlayerInHand]

    # not technically necessary to store, as it can be synthesized as max(eligibility)
    # + sum(current bets).
    pot: int

    stage: int
    deck: str

    def _finalize_betting(self, balances):
        max_bet = 0

        # Iterate over all players who have never bet
        for player in self.players:
            if player.eligibility is None:
                continue

            if balances[player.session_id] == 0:
                continue

            if player.has_option:
                # We're not done betting yet.
                return player.session_id

            if player.bet > max_bet:
                max_bet = player.bet

        # Iterate again over players who are below the max bet
        for player in self.players:
            if player.eligibility is None:
                # Player has folded
                continue

            if balances[player.session_id] == 0:
                continue

            if player.bet < max_bet:
                return player.session_id

        # The pot is good. Calculate eligibility. A player's eligibility is equal to
        # the sum of the bets < theirs, plus their bet times the number of bets
        # >= theirs.
        cumulative = 0
        bets_above = len(self.players)
        bets = sorted(self.players, key=lambda x: x.bet)

        for player in bets:
            round_bet = player.bet

            # We're moving to the next round: reset bets to zero
            player.bet = 0
            player.has_option = True

            if player.eligibility is not None:
                player.eligibility += cumulative + round_bet * bets_above

            cumulative += round_bet
            bets_above -= 1

        # None signifies that the pot is good and we're ready to move to the next
        # round.
        return None

    # Advances the game state machine
    def advance_state(self, room):
        balances = dict((k, p.balance) for (k, p) in room.players.items())

        while True:
            next_to_act = self._finalize_betting(balances)
            if next_to_act is not None:
                return

            # There is no next player to act. The pot is good.

            if self.stage == Stage.RIVER:
                break

            self.stage += 1

        self.pay_winners(room)

    def _get_player_idx(self, session_id):
        for idx, player in enumerate(self.players):
            if player.session_id != session_id:
                continue

            return idx

        raise KeyError(session_id)

    def get_player(self, session_id):
        for player in self.players:
            if player.session_id != session_id:
                continue

            return player

        raise KeyError(session_id)

    def fold(self, room, session_id):
        bettor = self.get_player(session_id)
        bettor.eligibility = None
        bettor.has_option = False

    def bet(self, room, session_id, value, lt_ok=False):
        bettor = self.get_player(session_id)
        needed = value - bettor.bet
        if needed < 0:
            return

        got = room.players[bettor.session_id].decrement_balance(needed)
        bettor.bet += got
        bettor.has_option = False
        self.pot += got
        return got

    @property
    def big_blind(self):
        players = self.players
        if len(players) == 2:
            return players[0]
        else:
            return players[1]

    @property
    def small_blind(self):
        players = self.players
        if len(players) == 2:
            return players[1]
        else:
            return players[0]

    def initialize(self, room):
        small_blind = room.small_blind

        # TODO: handle insolvency of the player
        pot = 0
        pot += self.bet(room, self.small_blind.session_id, small_blind)
        pot += self.bet(room, self.big_blind.session_id, small_blind * 2)

        self.pot = pot
        self.small_blind.has_option = True
        self.big_blind.has_option = True

    def _hole_cards_idx(self, idx):
        deck_index = idx * 4
        return self.deck[deck_index: deck_index + 4]

    def hole_cards(self, session_id):
        idx = self._get_player_idx(session_id)
        return self._hole_cards_idx(idx)

    @property
    def community_cards(self):
        start = len(self.players) * 4
        end = start + REVEALED_CARDS[self.stage] * 2

        return self.deck[start:end]

    def _get_final_hands(self):
        community_cards = self.community_cards

        for idx, player in enumerate(self.players):
            if player.eligibility is None:
                continue

            yield player.session_id, (player, community_cards + self._hole_cards_idx(idx))

    def pay_winners(self, room):
        final_hands = dict(self._get_final_hands())
        if len(final_hands) == 1:
            winning_players = list(final_hands.keys())
        else:
            winners = get_winners((s_id, cards) for (s_id, (_, cards)) in final_hands.items())
            winning_players = [s_id for (s_id, hand) in winners]

        for s_id in winning_players:
            # FIXME: definitely doesn't handle side-pots correctly.
            player, _ = final_hands[s_id]
            amount = player.eligibility
            room.players[s_id].increment_balance(amount)
            self.pot -= amount

        assert self.pot == 0


class Room(BaseModel):
    # After the room has been initialized, there will always be a Game present
    game: Optional[Game]

    # arranged in bet order: dealer last
    players: Dict[str, Player]

    small_blind: int = 1

    def player(self, session_id):
        return self.players[session_id]


class PydanticConsulKey(ConsulKey):
    def __init__(self, path, typ):
        self._path = path
        self._typ = typ
        super().__init__

    def get(self, *args, **kwargs):
        index, value = super().get(*args, **kwargs)
        if value is not NOT_PRESENT:
            value = self._typ(**value)

        return index, value

    def put(self, value, *args, **kwargs):
        value = value.dict(exclude_unset=True)
        return super().put(value, *args, **kwargs)


def _room(name):
    return PydanticConsulKey(f"/room/{name}", Room)


class CannotRegister(Exception):
    pass


def _get_game(room_name: str):
    return _room(room_name).get()[1].game


def register(room_name: str, session_id: str, player_name: str):
    room_state = _room(room_name)

    def mutate(state):
        if state is NOT_PRESENT:
            state = Room(players=dict(), small_blind=1)

        for key, player in state.players.items():
            if key == session_id:
                return NO_CHANGE

            if player.name == player_name:
                raise CannotRegister(f"The name {player_name} is taken in this room")

        if len(state.players) > 10:
            raise CannotRegister(f"This room already has {len(state.players)}")

        state.players[session_id] = Player(
            balance=100, name=player_name
        )
        return state

    return room_state.mutate(mutate)


class CannotStart(Exception):
    pass


def start(name: str, session_id: str):
    room_state = _room(name)

    def mutation(state):
        if state is NOT_PRESENT:
            raise RuntimeError

        if session_id not in state.players:
            # TODO: make a room admin?
            raise CannotStart("You are not joined to this room")

        if state.game is not None:
            # TODO: allow players to join for the next hand.
            raise CannotStart("The room has already started playing")

        if len(state.players) < 2:
            raise CannotStart("You're the only one here.")

        session_ids = list(state.players)
        random.shuffle(session_ids)
        in_hand = [PlayerInHand(session_id=s_id, bet=0) for s_id in session_ids]

        # Everything below here applies to every hand.

        cards_drawn = len(state.players) * 2 + 5
        number_deck = list(range(52))
        random.shuffle(number_deck)
        deck = ''.join(get_card(card) for card in number_deck[:cards_drawn])

        game = Game(
            players=in_hand,
            deck=deck,
            pot=0,
            stage=0,
        )
        state.game = game
        game.initialize(state)

        return state

    room_state.mutate(mutation)


def add_bet(name: str, session_id: str, value: int):
    room_state = _room(name)

    def mutation(room):
        room.game.bet(room, session_id, value)
        room.game.advance_state(room)
        return room

    room_state.mutate(mutation)


def fold(name: str, session_id: str):
    room_state = _room(name)

    def mutation(room):
        room.game.fold(room, session_id)
        room.game.advance_state(room)
        return room

    room_state.mutate(mutation)


class PlayerGameView(BaseModel):
    pot: int
    hole_cards: str
    community_cards: str


class PlayerRoomView(BaseModel):
    players: Dict[str, dict]
    game: Optional[PlayerGameView]


def get_player_view(room_name: str, session_id: str):
    room_state = _room(room_name).get()[1]

    ret = PlayerRoomView(
        players=dict(
            (player.name, dict(balance=player.balance))
            for player
            in room_state.players.values()
        )
    )

    if room_state.game is None:
        return ret

    game = room_state.game

    for idx, player in enumerate(game.players):
        if player.session_id == session_id:
            break
    else:
        raise KeyError("Cannot find session_id")

    deck_index = idx * 4
    community_start = len(game.players) * 4
    community_end = community_start + REVEALED_CARDS[game.stage] * 2

    ret.game = PlayerGameView(
        pot=game.pot,
        hole_cards=game.deck[deck_index: deck_index + 4],
        community_cards=game.deck[community_start:community_end],
    )
    return ret


def delete_room(name: str):
    _room(name).delete()
