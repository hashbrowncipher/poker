from poker import game
from poker.game import _get_game
from poker.game import _room
from poker.game import Game
from poker.game import PlayerInHand
from poker.game import Player
from poker.game import Stage
from poker.game import _make_deck
from random import Random


def test_play_hand(monkeypatch):
    monkeypatch.setattr(game, "random", Random(0))

    game.delete_room("test")
    game.register("test", "a", "blah a")
    game.register("test", "b", "blah b")
    game.register("test", "c", "other")

    view_a = game.get_player_view("test", "a")
    assert view_a.game is None

    game.start("test", "a")
    assert len(_room("test").get()[1].log) == 0
    deck = _get_game("test").deck

    view_a = game.get_player_view("test", "a")
    assert view_a.game.community_cards == ""
    assert view_a.game.hole_cards == deck[0:4]

    view_c = game.get_player_view("test", "c")
    assert view_c.game.hole_cards == deck[4:8]

    view_b = game.get_player_view("test", "b")
    assert view_b.game.hole_cards == deck[8:12]

    # Pre-flop
    game.add_bet("test", "b", 5)
    assert _get_game("test").stage == 0
    game.add_bet("test", "a", 5)
    assert _get_game("test").stage == 0
    game.add_bet("test", "c", 5)

    # Flop
    state = _get_game("test")
    assert state.stage == 1
    assert state.pot == 15

    view_a = game.get_player_view("test", "a")
    assert view_a.game.community_cards == deck[12:18]
    assert view_a.game.hole_cards == deck[0:4]

    game.add_bet("test", "a", 1)
    assert _get_game("test").stage == 1
    game.add_bet("test", "c", 2)
    assert _get_game("test").stage == 1
    game.add_bet("test", "b", 4)
    assert _get_game("test").stage == 1
    game.fold("test", "a")
    assert _get_game("test").stage == 1
    game.add_bet("test", "c", 4)

    # Turn
    view_a = game.get_player_view("test", "a")
    assert view_a.game.community_cards == deck[12:20]
    assert view_a.game.hole_cards == deck[0:4]

    state = _get_game("test")
    assert state.stage == 2
    assert state.pot == 24

    game.add_bet("test", "c", 0)
    game.add_bet("test", "b", 0)

    # River
    state = _get_game("test")
    assert state.stage == 3
    assert state.pot == 24

    final_hands = dict(state._get_final_hands())
    assert len(final_hands) == 2
    assert final_hands["c"][1] == deck[12:22]
    assert final_hands["c"][2] == deck[4:8]
    assert final_hands["b"][1] == deck[12:22]
    assert final_hands["b"][2] == deck[8:12]

    game.add_bet("test", "c", 1)
    game.add_bet("test", "b", 1)

    # Payout

    state = _get_game("test")
    assert len(_room("test").get()[1].log) == 1
    assert state.stage == 0
    assert state.pot == 3
    assert state.players[0].session_id == "c"
    assert state.players[0].bet == 1
    assert state.players[1].session_id == "b"
    assert state.players[1].bet == 2
    assert state.players[2].session_id == "a"
    assert state.players[2].bet == 0

    players = game.get_player_view("test", "a").players
    assert len(players) == 3
    assert players["other"]["balance"] == 115
    assert players["blah b"]["balance"] == 88
    assert players["blah a"]["balance"] == 94


class MockRoom:
    def __init__(self):
        self.log = list()
        self.players = dict()


def test_sidepot_random(monkeypatch):
    monkeypatch.setattr(game, "random", Random(0))

    test_players = [
        PlayerInHand(session_id="ls", bet=0, eligibility=3, has_option=False),
        # Want this player to win first
        PlayerInHand(session_id="w", bet=0, eligibility=5, has_option=False),
        # But then these players split because hilarious raisins
        PlayerInHand(session_id="wb", bet=0, eligibility=8, has_option=False),
        PlayerInHand(session_id="lb", bet=0, eligibility=8, has_option=False),
    ]
    finished_game = Game(
        players=test_players,
        stage=Stage.RIVER,
        pot=max(tp.eligibility for tp in test_players),
        deck=_make_deck(len(test_players)),
    )
    room = MockRoom()
    room.players = {
        "ls": Player(balance=0, name="loser", pending_balance=0),
        "w": Player(balance=0, name="winner", pending_balance=0),
        "lb": Player(balance=12, name="alsoloser", pending_balance=0),
        "wb": Player(balance=10, name="bigwinner", pending_balance=0),
    }

    finished_game.pay_winners(room)
    assert room.players["w"].balance == 5
    # Should split between wb and lb
    assert room.players["wb"].balance == 11
    assert room.players["lb"].balance == 13
    # Should leave 1 chip in the pot for the next round
    assert finished_game.pot == 1
    assert set(room.log[0].players.keys()) == set(("w", "lb", "wb"))


def test_normal_pot(monkeypatch):
    monkeypatch.setattr(game, "random", Random(0))

    test_players = [
        PlayerInHand(session_id="ls", bet=0, eligibility=5, has_option=False),
        PlayerInHand(session_id="w", bet=0, eligibility=5, has_option=False),
        PlayerInHand(session_id="lb", bet=0, eligibility=5, has_option=False),
    ]
    finished_game = Game(
        players=test_players,
        stage=Stage.RIVER,
        pot=max(tp.eligibility for tp in test_players),
        deck=_make_deck(len(test_players)),
    )
    room = MockRoom()
    room.players = {
        "ls": Player(balance=10, name="loser", pending_balance=0),
        "w": Player(balance=20, name="winner", pending_balance=0),
        "lb": Player(balance=40, name="alsoloser", pending_balance=0),
    }

    finished_game.pay_winners(room)
    assert room.players["ls"].balance == 10
    assert room.players["w"].balance == 25
    assert room.players["lb"].balance == 40


def test_sidepot_1():
    test_players = [
        PlayerInHand(session_id="ls", bet=0, eligibility=None, has_option=False),
        # Want w and wb to split the pot
        PlayerInHand(session_id="w", bet=0, eligibility=10, has_option=False),
        PlayerInHand(session_id="wb", bet=0, eligibility=20, has_option=False),
        # This player should unevenly lose their chips
        PlayerInHand(session_id="lb", bet=0, eligibility=20, has_option=False),
    ]
    finished_game = Game(
        players=test_players,
        stage=Stage.RIVER,
        pot=max(tp.eligibility or 0 for tp in test_players),
        #     ls     w      wb     lb     community
        deck=("2H3D" "AHAS" "ADAC" "XDXS" "3C9S5DJSQC"),
    )
    room = MockRoom()
    room.players = {
        "ls": Player(balance=7, name="loser", pending_balance=0),
        "w": Player(balance=0, name="winner", pending_balance=0),
        "lb": Player(balance=12, name="alsoloser", pending_balance=0),
        "wb": Player(balance=10, name="bigwinner", pending_balance=0),
    }

    finished_game.pay_winners(room)
    assert room.players["ls"].balance == 7
    assert room.players["w"].balance == 5
    assert room.players["wb"].balance == 25
    assert room.players["lb"].balance == 12
    assert finished_game.pot == 0


def test_showdown_no_show():
    test_players = [
        PlayerInHand(session_id="ls", bet=0, eligibility=None, has_option=False),
        # Want this player to win first
        PlayerInHand(session_id="w", bet=0, eligibility=20, has_option=False),
        PlayerInHand(session_id="lb", bet=0, eligibility=None, has_option=False),
    ]
    finished_game = Game(
        players=test_players,
        stage=Stage.RIVER,
        pot=max(tp.eligibility or 0 for tp in test_players),
        #     ls     w      wb     lb     community
        deck=("2H3D" "AHAS" "ADAC" "XDXS" "3C9S5DJSQC"),
    )
    room = MockRoom()
    room.players = {
        "ls": Player(balance=7, name="loser", pending_balance=0),
        "w": Player(balance=0, name="winner", pending_balance=0),
        "lb": Player(balance=12, name="alsoloser", pending_balance=0),
    }

    finished_game.pay_winners(room)
    assert room.players["ls"].balance == 7
    assert room.players["w"].balance == 20
    assert room.players["lb"].balance == 12
    assert finished_game.pot == 0
    assert set(room.log[0].players.keys()) == set(("w",))
    # Should not have shown down
    assert room.log[0].players["w"].hand is None
