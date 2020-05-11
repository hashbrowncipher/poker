from gevent.monkey import patch_all
patch_all()  # noqa: E402

from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.exceptions import HTTPException
from gevent.pywsgi import WSGIServer
from uuid import uuid4


from werkzeug.wrappers import Request
from werkzeug.wrappers import Response
from werkzeug.http import dump_cookie
import json
import logging

from . import game

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Data structures


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


url_map = Map([
    Rule('/room/<name>', endpoint=route_show_room),
    Rule('/room/<name>/start', endpoint=route_start_game),
    Rule('/room/<name>/bet', endpoint=route_bet),
])


def dispatch(request):
    urls = url_map.bind_to_environ(request.environ)
    endpoint, args = urls.match()
    return endpoint(request, **args)


def _get_session_id(request):
    try:
        return request.cookies["session_id"]
    except KeyError:
        pass

    return str(uuid4())


def identity_middleware(environ, start_response):
    request = Request(environ)
    session_id = _get_session_id(request)

    request.session_id = session_id

    response = dispatch(request)
    if not isinstance(response, Response):
        response = Response(
            json.dumps(response),
            headers=(("Content-Type", "application/json"),)
        )

    cookie_header = dump_cookie(
        key="session_id",
        value=session_id.encode("ascii"),
        max_age=86400,
        httponly=True,
        samesite="lax"
    )
    response.headers.add("Set-Cookie", cookie_header)
    return response(environ, start_response)


def exceptions_middleware(environ, start_response):
    try:
        return identity_middleware(environ, start_response)
    except HTTPException as ex:
        return ex(environ, start_response)
    except:  # noqa: E722
        logger.exception("Exception in request")


def main():
    server = WSGIServer(('0.0.0.0', 6543), exceptions_middleware)
    server.serve_forever()


if __name__ == '__main__':
    main()
