"""Tests for indexer/parse_code.py — tree-sitter JS/TS parser."""
from __future__ import annotations

from indexer.parse_code import parse_js, parse_ts


JS_OBJECT_LITERAL = """
const Controller = {
  async listDevices(req, res) {
    return res.json([]);
  },

  async getDevice(req, res) {
    const id = req.params.id;
    return res.json({ id });
  }
};

module.exports = Controller;
"""


JS_FUNCTION_DECL = """
function helperA(x) {
  return x + 1;
}

const helperB = (y) => y * 2;

function helperC() {}
"""


TS_CLASS = """
export class DeviceController {
  async findById(id: string): Promise<Device | null> {
    return null;
  }

  remove(id: string): void {}
}
"""


def test_parse_js_object_literal_methods():
    blocks = parse_js(JS_OBJECT_LITERAL, file_path="ctl.js")
    names = {b.name for b in blocks}
    assert "listDevices" in names
    assert "getDevice" in names


def test_parse_js_function_declarations_and_arrows():
    blocks = parse_js(JS_FUNCTION_DECL, file_path="helpers.js")
    names = {b.name for b in blocks}
    assert "helperA" in names
    assert "helperB" in names
    assert "helperC" in names


def test_parse_js_line_ranges_set():
    blocks = parse_js(JS_OBJECT_LITERAL, file_path="ctl.js")
    for b in blocks:
        assert b.line_start >= 1
        assert b.line_end >= b.line_start
        assert b.content.strip()


def test_parse_ts_class_methods():
    blocks = parse_ts(TS_CLASS, file_path="ctl.ts")
    names = {b.name for b in blocks}
    assert "findById" in names
    assert "remove" in names
