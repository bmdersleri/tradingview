from __future__ import annotations

import runpy

import pytest


def test_module_entrypoint_invokes_app() -> None:
    with pytest.raises(SystemExit):
        runpy.run_module("tvcli.__main__", run_name="__main__")
