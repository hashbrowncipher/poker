import json
import logging
import os
from importlib.resources import open_binary

from werkzeug.exceptions import HTTPException
from werkzeug.http import dump_cookie
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.wrappers import Request
from werkzeug.wrappers import Response

from base64 import urlsafe_b64encode
from hashlib import sha256

from poker import game

with open_binary("poker", "index.html") as fh:
    SPA_CONTENTS = fh.read()

with open_binary("poker", "chime.oga") as fh:
    CHIME_CONTENTS = fh.read()

logger = logging.getLogger(__name__)
COOKIE_KEY = "id"
CS_POLICY = "block-all-mixed-content; frame-ancestors 'none'; default-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net"


def base64url(b):
    return urlsafe_b64encode(b).split(b"=")[0].decode("ascii")


def hash_cookie_id(s):
    hashed = sha256(s.encode("utf-8")).digest()[:16]
    return base64url(hashed)


def _get_cookie_id(request):
    try:
        return request.cookies[COOKIE_KEY]
    except KeyError:
        pass

    return base64url(os.urandom(16))


def route_spa(request, room_name):
    return Response(
        SPA_CONTENTS, headers=(("Content-Type", "text/html; charset=utf-8"),)
    )


def route_chime(request):
    return Response(
        CHIME_CONTENTS,
        headers=(
            ("Content-Type", "audio/ogg"),
            ("Cache-Control", "public, max-age: 600"),
        )
    )


def route_show_room(request, room_name) -> Response:
    session_id = request.session_id
    index = request.args["index"]
    return game.show_room(room_name, session_id, index)


def route_join(request, room_name) -> Response:
    data = json.load(request.stream)
    try:
        game.register(room_name, request.session_id, data["name"].strip())
    except game.CannotRegister as ex:
        return Response(ex.args[0], status=400)


def route_bet(request, room_name) -> Response:
    data = json.load(request.stream)
    game.add_bet(room_name, request.session_id, data["amount"])


def route_fold(request, room_name) -> Response:
    game.fold(room_name, request.session_id)


def route_start(request, room_name) -> Response:
    game.start(room_name, request.session_id)


url_map = Map(
    [
        Rule("/r/<room_name>", endpoint=route_spa),
        Rule("/api/room/<room_name>", endpoint=route_show_room),
        Rule("/api/room/<room_name>/bet", endpoint=route_bet),
        Rule("/api/room/<room_name>/fold", endpoint=route_fold),
        Rule("/api/room/<room_name>/join", endpoint=route_join),
        Rule("/api/room/<room_name>/start", endpoint=route_start),
        Rule("/static/chime.oga", endpoint=route_chime),
    ]
)


def identity_middleware(environ, start_response):
    request = Request(environ)
    cookie_id = _get_cookie_id(request)
    request.session_id = hash_cookie_id(cookie_id)

    response = dispatch(request)
    if not isinstance(response, Response):
        response = Response(
            json.dumps(response), headers=(("Content-Type", "application/json"),)
        )

    cookie_header = dump_cookie(
        key=COOKIE_KEY,
        value=cookie_id.encode("ascii"),
        max_age=86400,
        httponly=True,
        samesite="lax",
    )
    response.headers.add("Set-Cookie", cookie_header)
    response.headers.add("Content-Security-Policy", CS_POLICY)
    response.headers.add("Referrer-Policy", "no-referrer")
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
