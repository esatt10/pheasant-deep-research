"""The console's access key: keyed access in pheasant-kb's shape.

A console that can start paid runs and read every trace is a credential
boundary. With ``PHEASANT_LAB_CONSOLE_TOKEN`` set, every ``/api`` call needs it
as a bearer token; the shell that asks for it stays public, because the bundle
cannot carry the key. Bound beyond loopback with no key, the console refuses
to start.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from pheasant_lab.console.server import Unauthenticated, is_loopback, serve

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY = "console-key-ünïcode-safe"


@pytest.fixture
def keyed(tmp_path: Path) -> Iterator[str]:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>console</title>")
    server = serve(
        host="127.0.0.1",
        port=0,
        project_root=REPO_ROOT,
        output_root=tmp_path / "runs",
        default_config="configs/demo.yaml",
        ui_dist=dist,
        token=KEY,
        env_file=None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _call(
    url: str, *, key: str | None = None, method: str = "GET", body: dict | None = None
) -> tuple[int, dict | str]:
    request = urllib.request.Request(url, method=method)
    if key is not None:
        request.add_header("Authorization", f"Bearer {key}".encode().decode("latin-1"))
    if method in {"POST", "PUT"}:
        request.data = json.dumps(body or {}).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body, status = response.read().decode(), response.status
    except urllib.error.HTTPError as error:
        body, status = error.read().decode(), error.code
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


def test_the_api_refuses_a_call_without_the_key(keyed: str) -> None:
    status, body = _call(f"{keyed}/api/runs")
    assert status == 401
    assert "access key" in body["detail"]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_wrong_key_is_refused_on_every_verb(keyed: str, method: str) -> None:
    path = "/api/runs" if method == "GET" else "/api/launches"
    status, _ = _call(f"{keyed}{path}", key="not-it", method=method)
    assert status == 401


def test_the_right_key_opens_the_api(keyed: str) -> None:
    status, runs = _call(f"{keyed}/api/runs", key=KEY)
    assert status == 200 and runs == []


def test_the_shell_and_the_auth_probe_stay_public(keyed: str) -> None:
    """The page that asks for the key must load without it."""

    status, page = _call(f"{keyed}/live/run-x")
    assert status == 200 and "<title>console</title>" in page
    status, auth = _call(f"{keyed}/api/auth")
    assert status == 200 and auth == {"required": True, "authenticated": False}
    status, auth = _call(f"{keyed}/api/auth", key=KEY)
    assert auth == {"required": True, "authenticated": True}


def test_a_header_byte_above_127_is_a_401_not_a_500(keyed: str) -> None:
    status, _ = _call(f"{keyed}/api/runs", key="clé")
    assert status == 401


def test_a_console_beyond_loopback_without_a_key_refuses_to_start(tmp_path: Path) -> None:
    with pytest.raises(Unauthenticated, match="PHEASANT_LAB_CONSOLE_TOKEN"):
        serve(
            host="0.0.0.0",
            port=0,
            project_root=REPO_ROOT,
            output_root=tmp_path,
            default_config="configs/demo.yaml",
            ui_dist=tmp_path,
        )


def test_an_open_loopback_console_needs_no_key(tmp_path: Path) -> None:
    """Standalone stays zero-configuration, as pheasant-kb's `role: all` does."""

    server = serve(
        host="127.0.0.1",
        port=0,
        project_root=REPO_ROOT,
        output_root=tmp_path / "runs",
        default_config="configs/demo.yaml",
        ui_dist=tmp_path,
        env_file=None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _call(f"{base}/api/runs")[0] == 200
        assert _call(f"{base}/api/auth")[1] == {"required": False, "authenticated": True}
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("host", "loopback"),
    [("127.0.0.1", True), ("::1", True), ("localhost", True), ("0.0.0.0", False), ("", False)],
)
def test_only_an_address_no_other_machine_reaches_is_loopback(host: str, loopback: bool) -> None:
    assert is_loopback(host) is loopback


# -- the logs surface, through the key ----------------------------------------


@pytest.fixture
def keyed_with_a_run(keyed: str, tmp_path: Path) -> tuple[str, Path]:
    run = tmp_path / "runs" / "run-old"
    (run / "raw").mkdir(parents=True)
    (run / "run-manifest.json").write_text("{}")
    (run / "raw" / "events.jsonl").write_text('{"sequence": 1}\n')
    return keyed, run


def test_deleting_a_run_needs_the_key_and_is_audited(keyed_with_a_run) -> None:
    base, run = keyed_with_a_run
    assert _call(f"{base}/api/runs/run-old", method="DELETE")[0] == 401
    assert run.is_dir()
    status, row = _call(f"{base}/api/runs/run-old", key=KEY, method="DELETE")
    assert status == 200 and row["kind"] == "run" and not run.exists()
    status, inventory = _call(f"{base}/api/logs", key=KEY)
    assert inventory["audit"][0]["target"] == "run-old"
    assert _call(f"{base}/api/runs/run-old", key=KEY, method="DELETE")[0] == 404


def test_a_run_file_is_read_through_the_api(keyed_with_a_run) -> None:
    base, _ = keyed_with_a_run
    status, page = _call(f"{base}/api/logs/runs/run-old?path=raw/events.jsonl&tail=1", key=KEY)
    assert status == 200 and page["lines"] == [{"n": 1, "text": '{"sequence": 1}'}]
    status, _ = _call(f"{base}/api/logs/runs/run-old?path=../../x", key=KEY)
    assert status == 404


def test_the_retention_policy_is_set_previewed_and_validated(keyed_with_a_run) -> None:
    base, run = keyed_with_a_run
    status, preview = _call(
        f"{base}/api/logs/retention/preview",
        key=KEY,
        method="POST",
        body={"policy": {"max_runs": 1}},
    )
    assert status == 200 and preview["plan"] == []
    status, refused = _call(
        f"{base}/api/logs/retention", key=KEY, method="PUT", body={"policy": {"run_days": -2}}
    )
    assert status == 400 and "run_days" in refused["detail"]
    status, saved = _call(
        f"{base}/api/logs/retention", key=KEY, method="PUT", body={"policy": {"auto_apply": True}}
    )
    assert status == 200 and saved["policy"]["auto_apply"] is True
    assert run.is_dir(), "saving a policy deleted something"
