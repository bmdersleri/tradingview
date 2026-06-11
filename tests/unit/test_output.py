from io import StringIO

from tvcli.errors import TvcliError
from tvcli.output import build_envelope, emit, envelope_from_error, render_table


def test_build_envelope_contains_schema_version() -> None:
    payload = build_envelope(command="version", data={"version": "1.0.0"})

    assert payload["schema_version"] == 1
    assert payload["ok"] is True
    assert payload["command"] == "version"


def test_render_table_formats_mappings() -> None:
    rendered = render_table({"name": "tvcli", "version": "1.0.0"})

    assert "name" in rendered
    assert "tvcli" in rendered


def test_emit_json_mode() -> None:
    stream = StringIO()
    emit(
        build_envelope(command="version", data={"version": "1.0.0"}),
        json_mode=True,
        stream=stream,
    )

    assert '"schema_version": 1' in stream.getvalue()


def test_emit_error_mode() -> None:
    stream = StringIO()
    emit(
        envelope_from_error("version", TvcliError("boom")),
        json_mode=False,
        stream=stream,
    )

    assert "GENERIC" in stream.getvalue()
