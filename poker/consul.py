import requests
import json
import logging
from base64 import b64decode
from os import environ

logger = logging.getLogger(__name__)

CONSUL_HTTP_ADDR = environ.get("CONSUL_HTTP_ADDR", "127.0.0.1:8500")

# Consul utilities


class _NotPresent:
    def __repr__(self):
        return "NOT_PRESENT"


NOT_PRESENT = _NotPresent()
NO_CHANGE = object()


class _Consul:
    @staticmethod
    def delete(path):
        resp = requests.delete(f"http://{CONSUL_HTTP_ADDR}/v1/kv{path}",)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def get(path, index=None, wait=None):
        params = dict()
        if index is not None:
            params["index"] = index

        if wait is not None:
            params["wait"] = wait

        resp = requests.get(f"http://{CONSUL_HTTP_ADDR}/v1/kv{path}", params=params,)
        index = resp.headers["X-Consul-Index"]
        logger.info("GET %s <- %s", path, resp.status_code)

        if resp.status_code == 404:
            return (index, NOT_PRESENT)
        elif resp.status_code == 200:
            value = resp.json()[0]["Value"]
            decoded = json.loads(b64decode(value))
            return index, decoded

        raise RuntimeError("Unexpected response", resp.status_code)

    @staticmethod
    def put(path, value, params=None, **kwargs):
        resp = requests.put(
            f"http://{CONSUL_HTTP_ADDR}/v1/kv{path}",
            params=params,
            json=value,
            **kwargs,
        )
        logger.info("PUT %s <- %s", path, resp.text)
        return resp.json()


class ConsulKey:
    def __init__(self, path):
        self._path = path

    def get(self, *args, **kwargs):
        ret = _Consul.get(self._path, *args, **kwargs)
        return ret

    def put(self, *args, **kwargs):
        return _Consul.put(self._path, *args, **kwargs)

    def delete(self, *args, **kwargs):
        return _Consul.delete(self._path, *args, **kwargs)

    def mutate(self, fn):
        while True:
            index, value = self.get()
            if value is NOT_PRESENT:
                index = 0

            new_value = fn(value)
            if new_value is NO_CHANGE:
                return value

            if self.put(new_value, params=dict(cas=index)):
                return new_value
