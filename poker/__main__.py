from gevent.monkey import patch_all
patch_all()  # noqa: E402

import logging

from gevent.pywsgi import WSGIServer
from poker.http import app


logging.basicConfig(level=logging.INFO)


"""
def _show_room(state: game.Room, player_id: str):
    players_list = list(state.players)
    my_index = players_list.index(player_id)

    ret = dict(
        index=my_index,
        balances=state.players.serialize(),
    )

    if "game" in state:
        game = state.game

        visible = dict(
            pot=game.pot,
            hole_cards=game.hole_cards(player_id),
            community_cards=game.community_cards,
        )
        ret["game"] = visible

    return ret


def route_show_room(request, name) -> Response:
    session_id = request.session_id

    try:
        new_state = game.register(name, session_id)
    except game.TooManyPlayers:
        return Response(
            "Couldn't join the room. There are too many players at the table"
        )

    return _show_room(new_state, session_id)


def route_start_game(request: Request, name: str) -> Response:
    session_id = request.session_id

    try:
        game.start(name, session_id)
    except game.CannotStart as e:
        return Response(e.args[0], status=400)

    return Response(status=204)


def route_bet(request: Request, name: str) -> Response:
    request.args["amount"]
"""


def main():
    server = WSGIServer(('0.0.0.0', 6543), app)
    server.serve_forever()


if __name__ == '__main__':
    main()
