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
from enum import IntEnum

logger = logging.getLogger(__name__)
random = SystemRandom()

# Deck management
_NUMBERS = "AKQJX98765432"
_SUITS = "SHCD"
_CARDS = "".join([number + suit for number, suit in product(_NUMBERS, _SUITS)])


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
    return _CARDS[index : index + 2]


class Player(BaseModel):
    name: str
    balance: int
    pending_balance: int

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

    def get_next_to_act(self, balances):
        max_bet = 0

        can_bet = []
        has_option = []

        # Iterate over all players who have never bet
        for player in self.players:
            if player.eligibility is None:
                # player has folded
                continue

            if balances[player.session_id] == 0:
                continue

            can_bet.append(player)
            if player.has_option:
                has_option.append(player)

            if player.bet > max_bet:
                max_bet = player.bet

        if len(can_bet) < 2:
            return None

        # To account for blinds, we must take the smallest bet, not the first bet
        # beneath the max_bet.
        smallest_bettor = min(can_bet, key=lambda player: player.bet)
        if smallest_bettor.bet < max_bet:
            return smallest_bettor

        # If all bets are equal, go in order of who has the option
        for player in has_option:
            return player.session_id

    def _finalize_betting(self, balances):
        # The pot is good. Calculate eligibility. A player's eligibility is equal to
        # the sum of the bets < theirs, plus their bet times the number of bets
        # >= theirs.
        cumulative = 0
        bets_above = len(self.players)
        bets = sorted(self.players, key=lambda x: x.bet)

        players_in = 0

        for player in bets:
            round_bet = player.bet

            # We're moving to the next round: reset bets to zero
            player.bet = 0
            player.has_option = True

            # TODO: I'm not sure eligibility is doing us any good.
            # It may be better to track the amount a player has paid into the pot.
            if player.eligibility is not None:
                player.eligibility += cumulative + round_bet * bets_above
                players_in += 1

            cumulative += round_bet
            bets_above -= 1

        return players_in

    # Advances the game state machine
    def advance_state(self, room):
        balances = room.get_balances()

        while True:
            next_to_act = self.get_next_to_act(balances)
            if next_to_act is not None:
                return

            # There is no next player to act. The pot is good.
            if self._finalize_betting(balances) < 2:
                # Everyone folded.
                break

            if self.stage == Stage.RIVER:
                break

            self.stage += 1

        self.pay_winners(room)
        room.new_game(self)

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
        if session_id != self.get_next_to_act(room):
            return

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
        return self.deck[deck_index : deck_index + 4]

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

            if player.eligibility <= 0:
                continue

            yield player.session_id, (
                player,
                community_cards + self._hole_cards_idx(idx),
            )

    def pay_winners(self, room):
        completed_game = CompletedGame(
            community_cards=self.community_cards, players=dict()
        )
        room.log.append(completed_game)

        final_hands = dict(self._get_final_hands())
        if len(final_hands) == 1:
            winning_players = list(final_hands.keys())
        else:
            winners = get_winners(
                (s_id, cards) for (s_id, (_, cards)) in final_hands.items()
            )
            logger.info("Winners:  %s", winners)
            winning_players = [s_id for (s_id, hand) in winners]

        for s_id in winning_players:
            # FIXME: definitely doesn't handle side-pots correctly.
            player, _ = final_hands[s_id]
            amount = player.eligibility
            completed_game.players[s_id] = PlayerAfterGame(hand=None, payout=amount)
            room.players[s_id].increment_balance(amount)
            self.pot -= amount

        assert self.pot == 0


class PlayerAfterGame(BaseModel):
    # Hand will be None if they don't have to show
    hand: Optional[str]

    payout: int


class CompletedGame(BaseModel):
    community_cards: str
    players: Dict[str, PlayerAfterGame]


class Room(BaseModel):
    # After the room has been initialized, there will always be a Game present
    game: Optional[Game]

    # arranged in bet order: dealer last
    players: Dict[str, Player]

    small_blind: int = 1
    log: List[CompletedGame]

    def get_name(self, session_id):
        return self.players[session_id].name

    def get_balances(self):
        return dict((k, p.balance) for (k, p) in self.players.items())

    def player(self, session_id):
        return self.players[session_id]

    def new_game(self, previous_game):
        if previous_game is None:
            session_ids = list(self.players)
            random.shuffle(session_ids)
            in_hand = [PlayerInHand(session_id=s_id, bet=0) for s_id in session_ids]
        else:
            rotated_players = previous_game.players[1:] + previous_game.players[0:1]
            in_hand = [
                PlayerInHand(session_id=player.session_id, bet=0)
                for player in rotated_players
            ]

        cards_drawn = len(self.players) * 2 + 5
        number_deck = list(range(52))
        random.shuffle(number_deck)

        deck = "".join(get_card(card) for card in number_deck[:cards_drawn])

        game = Game(players=in_hand, deck=deck, pot=0, stage=0,)
        self.game = game
        game.initialize(self)


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


class AlertException(Exception):
    pass


class CannotRegister(Exception):
    pass


def _get_game(room_name: str):
    return _room(room_name).get()[1].game


def register(room_name: str, session_id: str, player_name: str):
    room_state = _room(room_name)

    def mutate(state):
        if state is NOT_PRESENT:
            state = Room(players=dict(), small_blind=1, log=[])

        for key, player in state.players.items():
            if key == session_id:
                continue

            if player.name == player_name:
                raise CannotRegister(f"The name {player_name} is taken in this room")

        if session_id in state.players:
            state.players[session_id].name = player_name
            return state

        if len(state.players) > 10:
            raise CannotRegister(f"This room already has {len(state.players)}")

        state.players[session_id] = Player(
            balance=100, name=player_name, pending_balance=0
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

        state.new_game(None)
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
    next_to_act: str
    pot: int
    hole_cards: str
    community_cards: str
    players: list

    @staticmethod
    def _convert_to_unicode(cards):
        cards = iter(cards)
        while True:
            out = 0x1F0A0
            try:
                value = next(cards)
            except StopIteration:
                break

            if value == "A":
                out += 1
            elif value == "K":
                out += 0xE
            elif value == "Q":
                out += 0xD
            elif value == "J":
                out += 0xB
            elif value == "X":
                out += 0xA
            else:
                out += ord(value) - 0x30

            suit = next(cards)
            if suit == "H":
                out += 0x10
            elif suit == "D":
                out += 0x20
            elif suit == "C":
                out += 0x30

            yield chr(out)

    @staticmethod
    def convert_to_unicode(cards):
        return "".join(cards)

    def dict(self, *args, **kwargs):
        # Nasty hack that keeps tests passing
        val = super().dict(*args, **kwargs)
        val["hole_cards"] = self.convert_to_unicode(val["hole_cards"])
        val["community_cards"] = self.convert_to_unicode(val["community_cards"])
        return val


class PlayerRoomView(BaseModel):
    players: Dict[str, dict]
    game: Optional[PlayerGameView]
    log: List[CompletedGame]


def _show_room(session_id, room_state):
    if room_state is NOT_PRESENT:
        return None

    for game in room_state.log:
        game.players = dict(
            (room_state.get_name(session_id), result)
            for (session_id, result) in game.players.items()
        )

    ret = PlayerRoomView(
        players=dict(
            (player.name, dict(balance=player.balance))
            for s_id, player in room_state.players.items()
        ),
        log=room_state.log,
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
    next_to_act = game.get_next_to_act(room_state.get_balances())

    ret.game = PlayerGameView(
        next_to_act=room_state.player(next_to_act).name,
        pot=game.pot,
        hole_cards=game.deck[deck_index : deck_index + 4],
        community_cards=game.deck[community_start:community_end],
        players=[
            dict(
                name=room_state.player(p.session_id).name,
                bet=p.bet,
                eligibility=p.eligibility,
                has_option=p.has_option,
            )
            for p in game.players
        ],
    )
    return ret


def show_room(room_name: str, session_id: str, query_index: str):
    index, room_state = _room(room_name).get(index=query_index, wait="60s")
    value = _show_room(session_id, room_state)
    return dict(index=index, room=value.dict() if value is not None else None)


def get_player_view(room_name: str, session_id: str):
    room_state = _room(room_name).get()[1]
    return _show_room(session_id, room_state)


def delete_room(name: str):
    _room(name).delete()
