"""A local stand-in for the three Apify endpoints radar.sh uses.

It listens on 127.0.0.1 on a port the system picks, so nothing leaves the machine. It
serves:

  POST /v2/acts/<actor>/runs         start a run, answers 201 with {"data": {"id": ...}}
  GET  /v2/actor-runs/<run id>       status of a run
  GET  /v2/datasets/<dataset>/items  the items of a finished run

Every request is recorded with its path, bearer token and decoded body. Dataset items come
from the sample files in fixtures/: the Luma leg of a city returns that city's Luma sample,
the first Meetup keyword returns the Meetup sample, the Eventbrite leg returns the
Eventbrite sample, and every other leg returns an empty array.

Failure switches are lists of selectors. A selector is a dict with an optional "actor"
(the actor slug, as it appears in the URL) and an optional "body" (keys that must equal the
same keys of the start request body). An empty selector matches every start.

  start_401          the start answers 401
  start_402          the start answers 402
  start_500_once     the first matching start answers 500, later ones 201
  failed_run         the run ends FAILED
  bad_dataset        the dataset body is a JSON object, not an array

Status behaviour per leg is set with status_plans, a list of selectors that may also hold:

  cost               the usageTotalUsd value the status answers carry (any JSON value;
                     the key "no_cost": true leaves the field out)
  sequence           the answers of successive status requests for the run, each one of
                     "RUNNING", "SUCCEEDED" or "HTML502" (an HTML page with status 502); the
                     last one repeats. Without a sequence the first answer is SUCCEEDED.
  hold               path of a release file: while it exists the answer is RUNNING

Every request is recorded with "kind" ("start", "status" or "dataset") and "auth", the whole
Authorization header text (empty when the request had none).
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "fixtures")
RUN_COST = 0.001


def _city_slug_by_meetup_city():
    with open(os.path.join(ROOT, "cities.json"), encoding="utf-8") as fh:
        cities = json.load(fh)["active"]
    return {c["meetup"]["city"]: c["slug"] for c in cities}, \
        {c["luma_city"]: c["slug"] for c in cities if c.get("luma_city")}


def _matches(selector, actor, body):
    if "actor" in selector and selector["actor"] != actor:
        return False
    for key, want in selector.get("body", {}).items():
        if body.get(key) != want:
            return False
    return True


class StubApify:
    def __init__(self):
        self.requests = []
        self.start_401 = []
        self.start_402 = []
        self.start_500_once = []
        self.failed_run = []
        self.bad_dataset = []
        self.status_plans = []
        self._lock = threading.Lock()
        self._runs = {}
        self._fired_500 = set()
        self._server = None
        self._thread = None
        self._meetup_cities, self._luma_cities = _city_slug_by_meetup_city()

    @property
    def base(self):
        return "http://127.0.0.1:%d/v2" % self._server.server_address[1]

    def start(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _reply(self, code, payload):
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _record(self, body):
                auth = self.headers.get("Authorization", "")
                token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
                path = self.path.split("?")[0]
                kind = ("start" if self.command == "POST" else
                        "status" if "/actor-runs/" in path else
                        "dataset" if "/datasets/" in path else "other")
                with stub._lock:
                    stub.requests.append({"method": self.command, "path": self.path,
                                          "token": token, "body": body, "kind": kind,
                                          "auth": auth})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except ValueError:
                    body = None
                self._record(body)
                parts = self.path.split("?")[0].strip("/").split("/")
                if len(parts) == 4 and parts[0] == "v2" and parts[1] == "acts" and parts[3] == "runs":
                    code, payload = stub._start(parts[2], body or {})
                    self._reply(code, payload)
                else:
                    self._reply(404, {"error": {"type": "not-found"}})

            def do_GET(self):
                self._record(None)
                parts = self.path.split("?")[0].strip("/").split("/")
                if len(parts) == 3 and parts[1] == "actor-runs":
                    code, payload = stub._status(parts[2])
                elif len(parts) == 4 and parts[1] == "datasets" and parts[3] == "items":
                    code, payload = stub._items(parts[2])
                else:
                    code, payload = 404, {"error": {"type": "not-found"}}
                self._reply(code, payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join(timeout=5)

    # ---- request handling -------------------------------------------------

    def _start(self, actor, body):
        with self._lock:
            if any(_matches(s, actor, body) for s in self.start_401):
                return 401, {"error": {"type": "user-or-token-not-found"}}
            if any(_matches(s, actor, body) for s in self.start_402):
                return 402, {"error": {"type": "not-enough-usage-to-run-paid-actor"}}
            for index, selector in enumerate(self.start_500_once):
                if _matches(selector, actor, body) and index not in self._fired_500:
                    self._fired_500.add(index)
                    return 500, b"upstream error"
            run_id = "run-%d" % (len(self._runs) + 1)
            failed = any(_matches(s, actor, body) for s in self.failed_run)
            self._runs[run_id] = {"actor": actor, "body": body, "failed": failed,
                                  "dataset": "ds-%s" % run_id, "polls": 0}
        return 201, {"data": {"id": run_id}}

    def _status(self, run_id):
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return 404, {"error": {"type": "record-not-found"}}
            polls = run["polls"]
            run["polls"] += 1
            plan = {}
            for selector in self.status_plans:
                if _matches(selector, run["actor"], run["body"]):
                    plan = selector
                    break
        answer = "FAILED" if run["failed"] else "SUCCEEDED"
        hold = plan.get("hold")
        sequence = plan.get("sequence")
        if hold and os.path.exists(hold):
            answer = "RUNNING"
        elif sequence:
            answer = sequence[min(polls, len(sequence) - 1)]
        if answer == "HTML502":
            return 502, b"<html><body>502 Bad Gateway</body></html>"
        data = {"id": run_id, "status": answer, "defaultDatasetId": run["dataset"]}
        if not plan.get("no_cost"):
            data["usageTotalUsd"] = plan["cost"] if "cost" in plan else RUN_COST
        return 200, {"data": data}

    def _items(self, dataset):
        with self._lock:
            run = next((r for r in self._runs.values() if r["dataset"] == dataset), None)
        if run is None:
            return 404, {"error": {"type": "record-not-found"}}
        if any(_matches(s, run["actor"], run["body"]) for s in self.bad_dataset):
            return 200, {"error": "this is not an array"}
        return 200, self._sample_items(run["actor"], run["body"])

    def _sample_items(self, actor, body):
        name = None
        if actor == "matyascimbulka~luma-event-scraper":
            slug = self._luma_cities.get((body.get("cities") or [""])[0])
            name = "items-%s-luma-sample.json" % slug if slug else None
        elif actor == "filip_cicvarek~meetup-scraper":
            slug = self._meetup_cities.get(body.get("city"))
            if slug and body.get("searchKeyword") == "AI":
                name = "items-%s-meetup-sample.json" % slug
        elif actor == "aitorsm~eventbrite":
            name = "items-san-diego-eventbrite-sample.json"
        if name and os.path.isfile(os.path.join(FIXTURES, name)):
            with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
                return json.load(fh)
        return []

    # ---- helpers for tests ------------------------------------------------

    def starts(self):
        return [r for r in self.requests
                if r["method"] == "POST" and "/acts/" in r["path"]]

    def start_bodies(self, actor=None):
        out = []
        for r in self.starts():
            parts = r["path"].split("/")
            if actor is None or parts[3] == actor:
                out.append((parts[3], r["body"]))
        return out
