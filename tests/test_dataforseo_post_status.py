"""`_post` should optionally surface the real transport HTTP status.

The debug logger needs to distinguish an API-level empty (HTTP 200, DFS returned
nothing) from a network failure (no response). `_post` swallows non-200s into a
falsy None, so we add an opt-in `status_sink` dict that `_post` populates with
`http_status` / `error` for the *single* attempt — backward compatible: callers that
don't pass a sink see identical behavior.
"""

from __future__ import annotations

import requests

from skyward.data.dataforseo import DataForSEOClient


class _Resp:
    def __init__(self, status_code, json_data=None, raise_exc=None):
        self.status_code = status_code
        self._json = json_data
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise is not None:
            raise self._raise

    def json(self):
        return self._json


class _Session:
    def __init__(self, behavior):
        self._behavior = behavior

    def post(self, url, json=None, timeout=None):
        return self._behavior()


def _client():
    return DataForSEOClient(username="u", password="p")


def test_post_success_records_200_and_returns_json():
    sess = _Session(lambda: _Resp(200, json_data={"tasks": []}))
    sink: dict = {}
    out = _client()._post("http://x", [{}], session=sess, max_retries=1, status_sink=sink)
    assert out == {"tasks": []}
    assert sink["http_status"] == 200
    assert not sink.get("error")


def test_post_http_error_records_status_and_error():
    err = requests.exceptions.HTTPError("500 Server Error")
    sess = _Session(lambda: _Resp(500, raise_exc=err))
    sink: dict = {}
    out = _client()._post("http://x", [{}], session=sess, max_retries=1, status_sink=sink)
    assert out is None
    assert sink["http_status"] == 500
    assert "500" in sink["error"]


def test_post_network_error_leaves_status_none_records_error():
    def boom():
        raise requests.exceptions.ConnectionError("dns fail")

    sess = _Session(boom)
    sink: dict = {}
    out = _client()._post("http://x", [{}], session=sess, max_retries=1, status_sink=sink)
    assert out is None
    assert sink.get("http_status") is None
    assert "dns fail" in sink["error"]


def test_post_without_sink_is_unchanged():
    sess = _Session(lambda: _Resp(200, json_data={"ok": 1}))
    out = _client()._post("http://x", [{}], session=sess, max_retries=1)
    assert out == {"ok": 1}
