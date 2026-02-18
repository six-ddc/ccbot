#!/usr/bin/env python3
"""Generate a Homebrew formula for ccbot with all Python resource blocks.

Usage: python scripts/generate_homebrew_formula.py <version>

Requires: uv (used for dependency resolution and PyPI queries).
For local development, prefer: brew update-python-resources alexei-led/tap/ccbot
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PYPI_URL = "https://pypi.org/pypi/{name}/{version}/json"
POLL_INTERVAL = 10
POLL_TIMEOUT = 300

FORMULA_TEMPLATE = """\
class Ccbot < Formula
  include Language::Python::Virtualenv

  desc "Control Claude Code sessions remotely via Telegram"
  homepage "https://github.com/alexei-led/ccbot"
  url "{sdist_url}"
  sha256 "{sha256}"
  license "MIT"

  depends_on "python@3.14"
  depends_on "tmux"

{resources}

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/ccbot --version")
  end
end
"""


def pypi_json(name: str, version: str) -> dict:
    url = PYPI_URL.format(name=name, version=version)
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())


def sdist_info(name: str, version: str) -> tuple[str, str]:
    """Return (url, sha256) for a package's sdist on PyPI."""
    for f in pypi_json(name, version)["urls"]:
        if f["packagetype"] == "sdist":
            return f["url"], f["digests"]["sha256"]
    raise SystemExit(f"No sdist for {name}=={version}")


def wait_for_sdist(version: str) -> tuple[str, str]:
    """Poll PyPI until ccbot sdist is available."""
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        try:
            return sdist_info("ccbot", version)
        except SystemExit, urllib.error.HTTPError:
            if time.monotonic() >= deadline:
                raise
            print(f"Waiting for ccbot {version} on PyPI...", file=sys.stderr)
            time.sleep(POLL_INTERVAL)


def resolve_deps(version: str) -> list[tuple[str, str]]:
    """Use 'uv pip compile' to resolve all transitive deps."""
    with tempfile.TemporaryDirectory() as tmp:
        reqs_in = Path(tmp) / "in.txt"
        reqs_out = Path(tmp) / "out.txt"
        reqs_in.write_text(f"ccbot=={version}\n")
        subprocess.check_call(
            [
                "uv",
                "pip",
                "compile",
                str(reqs_in),
                "-o",
                str(reqs_out),
                "--no-header",
                "--no-annotate",
            ],
            stdout=subprocess.DEVNULL,
            stderr=sys.stderr,
        )
        deps = []
        for line in reqs_out.read_text().splitlines():
            line = line.split("#")[0].strip()
            if "==" in line:
                name, ver = line.split("==", 1)
                if name.strip().lower() != "ccbot":
                    deps.append((name.strip(), ver.strip()))
    return sorted(deps, key=lambda x: x[0].lower())


def resource_blocks(deps: list[tuple[str, str]]) -> str:
    blocks = []
    for name, ver in deps:
        url, sha = sdist_info(name, ver)
        blocks.append(
            f'  resource "{name}" do\n    url "{url}"\n    sha256 "{sha}"\n  end'
        )
    return "\n\n".join(blocks)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} <version>")

    version = sys.argv[1]
    print(f"Resolving ccbot {version}...", file=sys.stderr)
    sdist_url, sha256 = wait_for_sdist(version)
    deps = resolve_deps(version)
    print(f"Found {len(deps)} dependencies", file=sys.stderr)

    print(
        FORMULA_TEMPLATE.format(
            sdist_url=sdist_url,
            sha256=sha256,
            resources=resource_blocks(deps),
        )
    )


if __name__ == "__main__":
    main()
