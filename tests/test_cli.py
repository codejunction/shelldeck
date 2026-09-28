import json
import subprocess

import pytest
from typer.testing import CliRunner

from shelldeck import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLDECK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FC_CACHE_DIR", str(tmp_path / "fc"))
    monkeypatch.setitem(cli.CFG, "port", 5499)
    return tmp_path


def test_banner_loopback_has_no_network_url(capsys):
    cli._banner("127.0.0.1", "started")
    out = capsys.readouterr().out
    assert "|___/" in out and "started" in out
    assert "http://127.0.0.1:5499" in out
    assert "Network:  off" in out
    out.encode("ascii")  # piped output on Windows is cp1252; keep the banner ASCII


def test_banner_all_interfaces_shows_lan_url(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_lan_ip", lambda: "192.168.9.9")
    cli._banner("0.0.0.0", "foreground", "https")
    assert "https://192.168.9.9:5499" in capsys.readouterr().out


def test_web_starts_server_prints_banner_and_opens(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_health", lambda: None)
    monkeypatch.setattr(cli, "ensure_server", lambda: calls.append("server"))
    monkeypatch.setattr(cli, "_banner", lambda host, status, scheme="": calls.append(status))
    monkeypatch.setattr(cli, "open_window", lambda path="/": calls.append(path))
    assert runner.invoke(cli.app, ["web"]).exit_code == 0
    assert calls == ["server", "started", "/"]


def test_browser_by_default_app_window_on_request(monkeypatch):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(("browser", url)))
    monkeypatch.setattr(cli, "_browser", lambda: "edge.exe")
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **kw: opened.append(("app", argv[1])))
    monkeypatch.setitem(cli.CFG, "app", False)
    cli.open_window("/")
    monkeypatch.setitem(cli.CFG, "app", True)
    cli.open_window("/")
    assert opened == [("browser", "http://127.0.0.1:5499/"), ("app", "--app=http://127.0.0.1:5499/")]


def test_own_console_false_without_a_tty():
    # pytest captures stdin, so this is never "a console Windows opened just for sd"
    assert cli._own_console() is False


def test_serve_refuses_plain_http_off_loopback():
    r = runner.invoke(cli.app, ["serve", "--host", "0.0.0.0"])
    assert r.exit_code == 2 and "refusing to serve plain HTTP" in r.output


def test_serve_needs_cert_and_key_together(tmp_path):
    cert = tmp_path / "c.pem"
    cert.write_text("x")
    r = runner.invoke(cli.app, ["serve", "--cert", str(cert)])
    assert r.exit_code == 2 and "go together" in r.output


def test_render_markdown_and_code(tmp_path):
    md, py = tmp_path / "a.md", tmp_path / "b.py"
    md.write_text("# Title\n\nbody", encoding="utf-8")
    py.write_text("def hello():\n    return 1\n", encoding="utf-8")
    assert "Title" in runner.invoke(cli.app, ["render", str(md)]).output
    assert "hello" in runner.invoke(cli.app, ["render", str(py)]).output


@pytest.fixture
def codebase(tmp_path):
    root = tmp_path / "code"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        "def validate_token(token):\n    # check the auth token signature\n    return token.startswith('ok')\n",
        encoding="utf-8",
    )
    (root / "src" / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    return root


def test_search_formats(codebase):
    r = runner.invoke(cli.app, ["search", "validate token", "--root", str(codebase), "-f", "json"])
    assert r.exit_code == 0
    out = json.loads(r.output)
    top = out["results"][0]
    assert top["path"] == "src/auth.py"
    assert top["snippets"][0]["start_line"] == 1

    r = runner.invoke(cli.app, ["search", "validate token", "--root", str(codebase), "-f", "paths"])
    assert "src/auth.py:1-" in r.output

    r = runner.invoke(cli.app, ["search", "validate token", "--root", str(codebase)])
    assert r.exit_code == 0 and "src/auth.py" in r.output and "Confidence" in r.output


def test_search_rejects_bad_format(codebase):
    r = runner.invoke(cli.app, ["search", "x", "--root", str(codebase), "-f", "xml"])
    assert r.exit_code == 1
