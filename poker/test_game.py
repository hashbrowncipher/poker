from poker import game
from poker.game import _get_game
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

    game.add_bet("test", "c", 1)
    game.add_bet("test", "b", 1)

    # Payout

    state = _get_game("test")
    assert state.stage == 3
    assert state.pot == 0

    final_hands = dict(state._get_final_hands())
    assert len(final_hands) == 2
    assert final_hands["c"][1] == deck[12:22] + deck[4:8]
    assert final_hands["b"][1] == deck[12:22] + deck[8:12]

    players = game.get_player_view("test", "a").players
    assert len(players) == 3
    assert players["blah a"]["balance"] == 94
    assert players["blah b"]["balance"] == 90
    assert players["other"]["balance"] == 116
