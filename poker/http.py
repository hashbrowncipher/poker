from uuid import uuid4
import json
import logging
from importlib.resources import open_binary

from werkzeug.exceptions import HTTPException
from werkzeug.http import dump_cookie
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.wrappers import Request
from werkzeug.wrappers import Response

from poker import game

with open_binary("poker", "index.html") as fh:
    SPA_CONTENTS = fh.read()

logger = logging.getLogger(__name__)


def _get_session_id(request):
    try:
        return request.cookies["session_id"]
    except KeyError:
        pass

    return str(uuid4())


def route_spa(request, room_name):
    return Response(SPA_CONTENTS, headers=(
        ("Content-Type", "text/html; charset=utf-8"),
    ))


def route_show_room(request, room_name) -> Response:
    session_id = request.session_id
    index = request.args["index"]
    return game.show_room(room_name, session_id, index)


def route_join(request, room_name) -> Response:
    data = json.load(request.stream)
    try:
        game.register(room_name, request.session_id, data["name"])
    except game.CannotRegister as ex:
        return Response(ex.args[0], status=400)


def route_bet(request, room_name) -> Response:
    data = json.load(request.stream)
    game.register(room_name, request.session_id, data["amount"])


url_map = Map([
    Rule('/r/<room_name>', endpoint=route_spa),
    Rule('/api/room/<room_name>', endpoint=route_show_room),
    Rule('/api/room/<room_name>/bet', endpoint=route_bet),
    Rule('/api/room/<room_name>/join', endpoint=route_join),
])


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


def dispatch(request):
    urls = url_map.bind_to_environ(request.environ)
    endpoint, args = urls.match()
    return endpoint(request, **args)


app = exceptions_middleware
