"""Offline regression checks for the live evaluation reader; no provider calls."""

import importlib.util
import json
from pathlib import Path

import pytest

from venturi.models import write_json
from venturi.visual_review import VisualObservations

SPEC = importlib.util.spec_from_file_location(
    "codex_evaluation", Path(__file__).resolve().parents[1] / "scripts/check_codex_assistance.py"
)
EVALUATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATION)


def retained_response(folder, events):
    write_json(folder / "execution.json", {"exit_code": 0})
    (folder / "events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events), encoding="utf-8"
    )
    (folder / "response.json").write_text(
        '{"observations":[],"limitations":["Δp ≈ 2 Pa; three slices cannot prove validation."]}',
        encoding="utf-8",
    )


def test_reads_literal_unicode_without_platform_default_encoding(tmp_path, monkeypatch):
    retained_response(
        tmp_path,
        [
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Δp ≈ 2 Pa"}},
            {"type": "turn.completed"},
        ],
    )
    original = Path.read_text

    def ascii_default(path, encoding=None, errors=None):
        return original(path, encoding=encoding or "ascii", errors=errors)

    monkeypatch.setattr(Path, "read_text", ascii_default)
    result = EVALUATION.read_response(tmp_path, VisualObservations)
    assert "Δp ≈" in result.limitations[0]


@pytest.mark.parametrize(
    "event",
    [
        {"type": "item.completed", "item": {"type": "command_execution"}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call"}},
    ],
)
def test_rejects_tool_activity_even_when_final_json_is_valid(tmp_path, event):
    retained_response(tmp_path, [event, {"type": "turn.completed"}])
    with pytest.raises(AssertionError, match="tool activity"):
        EVALUATION.read_response(tmp_path, VisualObservations)


def test_rejects_incomplete_turn_with_saved_json(tmp_path):
    retained_response(tmp_path, [{"type": "turn.started"}])
    with pytest.raises(AssertionError, match="completed turn"):
        EVALUATION.read_response(tmp_path, VisualObservations)
