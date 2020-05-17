import logging
from random import SystemRandom
from itertools import cycle
from pydantic import BaseModel
from typing import Dict
from typing import Optional
from typing import List
from typing import Tuple

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
        value = min(self.balance, value)
        self.balance -= value
        return value

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
        player_loop = cycle(self.players)

        # Find the highest bettor
        high_bettor = next(player_loop)
        for player in player_loop:
            if player is high_bettor:
                break

            if player.bet > high_bettor.bet:
                high_bettor = player

        # By exiting the loop after seeing high bettor, we have already rotated the
        # players list into the appropriate position.

        can_bet = 0
        for _, player in zip(self.players, player_loop):
            if player.eligibility is None:
                continue

            if balances[player.session_id] == 0:
                continue

            if player.bet < high_bettor.bet:
                return player.session_id

            can_bet += 1

        # All of the players are equal. Look for one with the option

        if can_bet >= 2:
            for player in self.players:
                if not player.has_option:
                    continue

                if player.eligibility is None:
                    continue

                if balances[player.session_id] == 0:
                    continue

                return player.session_id

        return None

    def _should_do_more_betting_rounds(self, balances):
        """Given that the pot is good for this round, returns whether there should be
        more betting action for this entire hand."""

        # There must be two non-folded players.
        count = 0
        for player in self.players:
            if player.eligibility is None:
                continue

            count += 1
            if count == 2:
                return True

        return False

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

            if not self._should_do_more_betting_rounds(balances):
                logger.info("Should not do more betting rounds")
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
        self._check_can_bet(session_id, room)
        bettor = self.get_player(session_id)
        bettor.eligibility = None
        bettor.has_option = False

    def _check_can_bet(self, session_id, room):
        if session_id != self.get_next_to_act(room.get_balances()):
            return None

    def _bet(self, room, session_id, value):
        bettor = self.get_player(session_id)
        needed = value - bettor.bet
        if needed < 0:
            return None

        got = room.players[bettor.session_id].decrement_balance(needed)
        bettor.bet += got
        bettor.has_option = False
        self.pot += got
        return got

    def bet(self, room, session_id, value, lt_ok=False):
        self._check_can_bet(session_id, room)
        self._bet(room, session_id, value)

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
        pot += self._bet(room, self.small_blind.session_id, small_blind)
        pot += self._bet(room, self.big_blind.session_id, small_blind * 2)

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
                player.copy(),
                community_cards,
                self._hole_cards_idx(idx),
            )

    def pay_winners(self, room):
        completed_game = CompletedGame(
            community_cards=self.community_cards, players=dict()
        )
        room.log.append(completed_game)

        # Maps player ids -> (player, community, hole_cards)
        final_hands: Dict[str, Tuple[PlayerInHand, str, str]] = dict(
            self._get_final_hands()
        )

        max_payouts = len(final_hands)
        while self.pot > 0:
            if len(final_hands) == 1:
                winning_players = list(final_hands.keys())
            else:
                winners = get_winners(
                    (s_id, community + hole)
                    for (s_id, (_, community, hole)) in final_hands.items()
                )
                logger.info("Winners:  %s", winners)
                winning_players = [s_id for (s_id, _) in winners]

            eliminated = []
            # Eligibility is odd, we have to take the minimum of the
            # winning eligibility and divide it between the winners
            amount = min(final_hands[s][0].eligibility for s in winning_players) // len(
                winning_players
            )

            # We're trying to split fewer chips than we have winners
            # Leave the remaining winnings for the next game's pot
            if amount == 0:
                return

            def pay_player(s_id, amount):
                # Winner has to show down to win
                # TODO(joey): Losers with a smaller player index who have
                # not folded should also show down
                if len(final_hands) > 1:
                    hand = final_hands[s_id][2]
                else:
                    hand = None

                if s_id not in completed_game.players:
                    completed_game.players[s_id] = PlayerAfterGame(hand=hand, payout=0)

                completed_game.players[s_id].payout += amount
                room.players[s_id].increment_balance(amount)

            for s_id in winning_players:
                pay_player(s_id, amount)
                # Now remove all players that are no longer eligible to win
                for (p_id, (p, _, _)) in final_hands.items():
                    p.eligibility -= amount
                    if p.eligibility <= 0:
                        eliminated.append(p_id)
                self.pot -= amount
                print(self.pot)

            for p_elim in eliminated:
                del final_hands[p_elim]

            max_payouts -= 1
            # Infinite loop bug?
            assert max_payouts >= 0

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

    def _add_pending_players_to_game_list(self, players):
        in_game = set(player.session_id for player in players)
        pending_players = [
            PlayerInHand(session_id=session_id, bet=0)
            for session_id
            in self.players.keys()
            if session_id not in in_game
        ]

        random.shuffle(pending_players)
        return pending_players + players


    def new_game(self, previous_game):
        if previous_game is None:
            in_hand = []
            previous_pot = 0
        else:
            rotated_players = previous_game.players[1:] + previous_game.players[0:1]
            in_hand = [
                PlayerInHand(session_id=player.session_id, bet=0)
                for player in rotated_players
            ]
            previous_pot = previous_game.pot

        in_hand = self._add_pending_players_to_game_list(in_hand)

        # TODO(joey): I think we should be doing len of in hand but ...
        deck = _make_deck(len(self.players))
        game = Game(players=in_hand, deck=deck, pot=previous_pot, stage=0,)
        self.game = game
        game.initialize(self)


def _make_deck(num_players):
    # Two hole cards per player plus the community cards
    cards_drawn = num_players * 2 + 5
    number_deck = list(range(52))
    random.shuffle(number_deck)
    return "".join(get_card(card) for card in number_deck[:cards_drawn])


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

        if len(player_name) > 64:
            raise CannotRegister("Your name is tooooooo lonnnnng.")

        for key, player in state.players.items():
            if key == session_id:
                continue

            if player.name == player_name:
                raise CannotRegister(f"The name {player_name} is taken in this room")

        if session_id in state.players:
            state.players[session_id].name = player_name
            return state

        if len(state.players) > 10:
            raise CannotRegister(f"This room already has {len(state.players)} players")

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


def add_player_balance(name: str, session_id: str, amount: str):
    room_state = _room(name)

    def mutation(room):
        room.player(session_id).increment_balance(amount)
        return room

    room_state.mutate(mutation)


class PlayerGameView(BaseModel):
    next_to_act: str
    pot: int
    hole_cards: str
    community_cards: str
    players: list


class PlayerRoomView(BaseModel):
    name: Optional[str]
    players: Dict[str, dict]
    game: Optional[PlayerGameView]
    log: List[CompletedGame]


def _get_game_view(session_id, room_state):
    game = room_state.game
    if game is None:
        return None

    for idx, player in enumerate(game.players):
        if player.session_id == session_id:
            break
    else:
        # Player is not in this game
        # They are "in the waiting room"
        return None

    deck_index = idx * 4
    community_start = len(game.players) * 4
    community_end = community_start + REVEALED_CARDS[game.stage] * 2
    next_to_act = game.get_next_to_act(room_state.get_balances())

    return PlayerGameView(
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


def _show_room(session_id, room_state):
    if room_state is NOT_PRESENT:
        return None

    myself = room_state.players.get(session_id, None)
    for game in room_state.log:
        game.players = dict(
            (room_state.get_name(session_id), result)
            for (session_id, result) in game.players.items()
        )

    return PlayerRoomView(
        name=myself.name if myself else None,
        players=dict(
            (player.name, dict(balance=player.balance))
            for s_id, player in room_state.players.items()
        ),
        log=room_state.log,
        game=_get_game_view(session_id, room_state),
    )


def show_room(room_name: str, session_id: str, query_index: str):
    index, room_state = _room(room_name).get(index=query_index, wait="60s")
    value = _show_room(session_id, room_state)
    return dict(index=index, room=value.dict() if value is not None else None)


def get_player_view(room_name: str, session_id: str):
    room_state = _room(room_name).get()[1]
    return _show_room(session_id, room_state)


def delete_room(name: str):
    _room(name).delete()
