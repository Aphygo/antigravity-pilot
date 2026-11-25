#!/usr/bin/env python3
"""Comprehensive zero-dependency cross-platform test suite for antigravity-pilot.

Covers:
  - scripts/png_overlay.py (all 5 PNG color types x 5 filter types, multi-IDAT,
    tRNS, 16-bit downsampling, crop_rgba, detect_opaque_bounds, render_grid,
    render_som_annotations across 1.0/1.25/2.0 DPI scales, edge clipping, roundtrip)
  - scripts/pilot_ctl.py (Risk-Guard EN+SV & Unicode boundaries, --confirm-risky
    before/after subcommand, --trace / PILOT_CTL_TRACE JSONL, window-tile across
    all 6 positions + multi-display + negative coords + OS margins, X11 offscreen
    screenshot crop fallback, full recipe lifecycle & path-traversal defense,
    Windows & Linux platform simulations)
  - scripts/sandbox_ctl.py (name/port validation, loopback port allocation,
    list --json parsing, url formatting, missing Docker error handling)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

PLUGIN_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PLUGIN_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pilot_ctl  # noqa: E402
import png_overlay  # noqa: E402
import sandbox_ctl  # noqa: E402


# ---------------------------------------------------------------------------
# Pure-stdlib synthetic PNG encoder helper for all 5 color types x 5 filters
# ---------------------------------------------------------------------------


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _apply_png_filter(
    raw_rows: Sequence[bytes],
    bpp: int,
    filter_type: int,
) -> bytes:
    """Encode raw scanlines with the requested PNG filter type (0..4)."""
    out = bytearray()
    stride = len(raw_rows[0]) if raw_rows else 0
    prev = bytes(stride)
    for row in raw_rows:
        out.append(filter_type)
        if filter_type == 0:
            out.extend(row)
        elif filter_type == 1:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                out.append((row[i] - left) & 0xFF)
        elif filter_type == 2:
            for i in range(stride):
                out.append((row[i] - prev[i]) & 0xFF)
        elif filter_type == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                out.append((row[i] - ((left + prev[i]) >> 1)) & 0xFF)
        elif filter_type == 4:
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                out.append((row[i] - _paeth_predictor(a, b, c)) & 0xFF)
        else:
            raise ValueError(f"Unsupported filter_type={filter_type}")
        prev = row
    return bytes(out)


def _build_synthetic_png(
    width: int,
    height: int,
    color_type: int,
    raw_rows: Sequence[bytes],
    *,
    bit_depth: int = 8,
    filter_type: int = 0,
    palette: Optional[bytes] = None,
    trns: Optional[bytes] = None,
    idat_splits: int = 1,
    interlace: int = 0,
) -> bytes:
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bpp = channels * (bit_depth // 8)
    filtered = _apply_png_filter(raw_rows, bpp=bpp, filter_type=filter_type)
    compressed = zlib.compress(filtered, level=6)

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, interlace)
    parts = [png_overlay.PNG_SIGNATURE, png_overlay._png_chunk(b"IHDR", ihdr)]
    if palette is not None:
        parts.append(png_overlay._png_chunk(b"PLTE", palette))
    if trns is not None:
        parts.append(png_overlay._png_chunk(b"tRNS", trns))

    splits = max(1, min(idat_splits, len(compressed)))
    chunk_size = max(1, (len(compressed) + splits - 1) // splits)
    for start in range(0, len(compressed), chunk_size):
        parts.append(png_overlay._png_chunk(b"IDAT", compressed[start : start + chunk_size]))
    parts.append(png_overlay._png_chunk(b"IEND", b""))
    return b"".join(parts)


def _capture_pilot_main(
    argv: Sequence[str],
    *,
    stdin_text: Optional[str] = None,
) -> Tuple[int, str, str]:
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
        if stdin_text is not None:
            with mock.patch.object(sys, "stdin", io.StringIO(stdin_text)):
                rc = pilot_ctl.main(list(argv))
        else:
            rc = pilot_ctl.main(list(argv))
    return rc, stdout_buf.getvalue(), stderr_buf.getvalue()


class _FakeWindowsPath(pathlib.PureWindowsPath):
    """Cross-Python-version (3.9-3.14) PureWindowsPath helper for Windows simulation on POSIX."""

    @classmethod
    def home(cls) -> "_FakeWindowsPath":
        userprofile = os.environ.get("USERPROFILE") or r"C:\Users\tester"
        return cls(userprofile)

    def expanduser(self) -> "_FakeWindowsPath":
        s = str(self)
        if s == "~" or s.startswith("~\\") or s.startswith("~/"):
            return self.home() / s[2:] if len(s) > 1 else self.home()
        return self


# ---------------------------------------------------------------------------
# 1. png_overlay.py tests
# ---------------------------------------------------------------------------


class TestPngOverlay(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_color_types_x_all_filter_types(self) -> None:
        """All 5 PNG color types (0, 2, 3, 4, 6) x all 5 filter types (0..4) decode pixel-exact."""
        w, h = 9, 7

        # Palette for color type 3 (4 colors + partial tRNS)
        palette = bytes([
            10, 20, 30,
            200, 50, 75,
            30, 180, 90,
            240, 220, 10,
        ])
        plte_trns = bytes([255, 128, 0])  # index 3 defaults to alpha=255

        for color_type in (0, 2, 3, 4, 6):
            raw_rows: List[bytes] = []
            expected = bytearray()
            for y in range(h):
                row = bytearray()
                for x in range(w):
                    v = (x * 29 + y * 53 + 17) & 0xFF
                    if color_type == 0:
                        row.append(v)
                        expected.extend((v, v, v, 255))
                    elif color_type == 2:
                        r, g, b = v, (v * 3 + 11) & 0xFF, (255 - v) & 0xFF
                        row.extend((r, g, b))
                        expected.extend((r, g, b, 255))
                    elif color_type == 3:
                        idx = (x + y * 2) % 4
                        row.append(idx)
                        pr = palette[idx * 3]
                        pg = palette[idx * 3 + 1]
                        pb = palette[idx * 3 + 2]
                        pa = plte_trns[idx] if idx < len(plte_trns) else 255
                        expected.extend((pr, pg, pb, pa))
                    elif color_type == 4:
                        alpha = (x * 31 + y * 19) & 0xFF
                        row.extend((v, alpha))
                        expected.extend((v, v, v, alpha))
                    elif color_type == 6:
                        r = v
                        g = (x * 41 + y * 7) & 0xFF
                        b = (y * 37 + 99) & 0xFF
                        a = (x * 17 + y * 23 + 100) & 0xFF
                        row.extend((r, g, b, a))
                        expected.extend((r, g, b, a))
                raw_rows.append(bytes(row))

            for filter_type in (0, 1, 2, 3, 4):
                with self.subTest(color_type=color_type, filter_type=filter_type):
                    png_bytes = _build_synthetic_png(
                        w,
                        h,
                        color_type,
                        raw_rows,
                        filter_type=filter_type,
                        palette=palette if color_type == 3 else None,
                        trns=plte_trns if color_type == 3 else None,
                    )
                    p = self.tmp_dir / f"ct{color_type}_f{filter_type}.png"
                    p.write_bytes(png_bytes)

                    dw, dh = png_overlay.read_png_dimensions(p)
                    self.assertEqual((dw, dh), (w, h))

                    rw, rh, rgba = png_overlay.read_png(p)
                    self.assertEqual((rw, rh), (w, h))
                    self.assertEqual(bytes(rgba), bytes(expected))

    def test_multi_idat_chunks(self) -> None:
        """PNG data split across multiple IDAT chunks decodes identically."""
        w, h = 16, 12
        raw_rows = [
            bytes([(x * 13 + y * 29 + c * 7) & 0xFF for x in range(w) for c in range(4)])
            for y in range(h)
        ]
        expected = b"".join(raw_rows)
        png_bytes = _build_synthetic_png(w, h, 6, raw_rows, filter_type=4, idat_splits=5)
        self.assertGreaterEqual(png_bytes.count(b"IDAT"), 4)

        p = self.tmp_dir / "multi_idat.png"
        p.write_bytes(png_bytes)
        rw, rh, rgba = png_overlay.read_png(p)
        self.assertEqual((rw, rh), (w, h))
        self.assertEqual(bytes(rgba), expected)

    def test_trns_grayscale_and_rgb_and_16bit(self) -> None:
        """tRNS key transparency (types 0 & 2) and 16-bit downsampling decode accurately."""
        # Grayscale with tRNS key=42
        gray_rows = [bytes([10, 42, 200]), bytes([42, 99, 42])]
        p_gray = self.tmp_dir / "gray_trns.png"
        p_gray.write_bytes(
            _build_synthetic_png(3, 2, 0, gray_rows, trns=struct.pack(">H", 42))
        )
        w, h, rgba = png_overlay.read_png(p_gray)
        self.assertEqual((w, h), (3, 2))
        alphas = [rgba[i * 4 + 3] for i in range(6)]
        self.assertEqual(alphas, [255, 0, 255, 0, 255, 0])

        # Truecolor RGB with tRNS key=(1, 2, 3)
        rgb_rows = [bytes([1, 2, 3, 4, 5, 6])]
        p_rgb = self.tmp_dir / "rgb_trns.png"
        p_rgb.write_bytes(
            _build_synthetic_png(2, 1, 2, rgb_rows, trns=struct.pack(">HHH", 1, 2, 3))
        )
        _, _, rgba2 = png_overlay.read_png(p_rgb)
        self.assertEqual(list(rgba2), [1, 2, 3, 0, 4, 5, 6, 255])

        # 16-bit RGBA downsampled to 8-bit high bytes
        row16 = struct.pack(">HHHHHHHH", 0xAA11, 0xBB22, 0xCC33, 0xFF44, 0x1099, 0x2088, 0x3077, 0x8066)
        p_16 = self.tmp_dir / "rgba16.png"
        p_16.write_bytes(_build_synthetic_png(2, 1, 6, [row16], bit_depth=16, filter_type=1))
        _, _, rgba16 = png_overlay.read_png(p_16)
        self.assertEqual(list(rgba16), [0xAA, 0xBB, 0xCC, 0xFF, 0x10, 0x20, 0x30, 0x80])

    def test_corrupt_and_interlaced_png_rejected(self) -> None:
        bad_sig = self.tmp_dir / "bad.png"
        bad_sig.write_bytes(b"NOTAPNG123456789012345678")
        with self.assertRaises(ValueError):
            png_overlay.read_png(bad_sig)
        with self.assertRaises(ValueError):
            png_overlay.read_png_dimensions(bad_sig)

        interlaced = self.tmp_dir / "interlaced.png"
        interlaced.write_bytes(_build_synthetic_png(2, 2, 6, [b"\xff" * 8, b"\xff" * 8], interlace=1))
        with self.assertRaises(ValueError):
            png_overlay.read_png(interlaced)

        # Bad CRC
        good = bytearray(_build_synthetic_png(2, 2, 6, [b"\xff" * 8, b"\xff" * 8]))
        good[-1] ^= 0xFF
        bad_crc = self.tmp_dir / "bad_crc.png"
        bad_crc.write_bytes(bytes(good))
        with self.assertRaises(ValueError):
            png_overlay.read_png(bad_crc)

    def test_detect_opaque_bounds(self) -> None:
        # 1. All-opaque fast path
        w, h = 100, 80
        opaque_buf = bytearray(bytes((30, 40, 50, 255)) * (w * h))
        self.assertEqual(png_overlay.detect_opaque_bounds(opaque_buf, w, h), (0, 0, w, h))

        # 2. Translucent drop-shadow border around an opaque window body
        shadow_buf = bytearray(bytes((0, 0, 0, 40)) * (w * h))
        png_overlay.fill_rect(shadow_buf, w, h, 14, 10, 70, 55, (255, 255, 255, 255))
        self.assertEqual(png_overlay.detect_opaque_bounds(shadow_buf, w, h), (14, 10, 70, 55))

        # 3. Fully transparent fallback
        empty_buf = bytearray(w * h * 4)
        self.assertEqual(png_overlay.detect_opaque_bounds(empty_buf, w, h), (0, 0, w, h))

    def test_crop_rgba(self) -> None:
        w, h = 10, 8
        buf = bytearray(w * h * 4)
        for y in range(h):
            for x in range(w):
                off = (y * w + x) * 4
                buf[off : off + 4] = bytes((x, y, 123, 255))

        cw, ch, cropped = png_overlay.crop_rgba(buf, w, h, 2, 3, 4, 3)
        self.assertEqual((cw, ch), (4, 3))
        self.assertEqual(list(cropped[0:4]), [2, 3, 123, 255])
        self.assertEqual(list(cropped[(2 * 4 + 3) * 4 : (2 * 4 + 3) * 4 + 4]), [5, 5, 123, 255])

        # Clamped out-of-bounds crop
        cw2, ch2, cropped2 = png_overlay.crop_rgba(buf, w, h, -2, 6, 5, 10)
        self.assertEqual((cw2, ch2), (3, 2))
        self.assertEqual(list(cropped2[0:4]), [0, 6, 123, 255])

        # Empty crop raises ValueError
        with self.assertRaises(ValueError):
            png_overlay.crop_rgba(buf, w, h, 20, 20, 5, 5)

    def test_render_grid_scales(self) -> None:
        for scale in (1.0, 1.25, 2.0):
            with self.subTest(scale=scale):
                w, h = int(320 * scale), int(240 * scale)
                base = bytes((20, 20, 20, 255)) * (w * h)
                buf = bytearray(base)
                png_overlay.render_grid(buf, w, h, step=100, scale_x=scale, scale_y=scale)
                self.assertEqual(len(buf), w * h * 4)
                self.assertNotEqual(bytes(buf), base)
                # Pixel along vertical line at logical x=100 must have changed
                px = int(round(100 * scale))
                off = (10 * w + px) * 4
                self.assertNotEqual(tuple(buf[off : off + 4]), (20, 20, 20, 255))

    def test_render_som_annotations_scales_and_edge_clipping(self) -> None:
        elements = [
            {"label": "e1", "bounds": {"x": 100, "y": 50, "width": 80, "height": 30}},
            # Near top-left edge (badge must shift inward)
            {"label": "e2", "bounds": {"x": 100, "y": 50, "width": 20, "height": 15}},
            # Partially clipped off right/bottom edge
            {"label": "e3", "bounds": {"x": 380, "y": 230, "width": 60, "height": 50}},
            # Completely off-screen (should be skipped)
            {"label": "e4", "bounds": {"x": 2000, "y": 2000, "width": 50, "height": 20}},
            # Degenerate zero-size (should be skipped)
            {"label": "e5", "bounds": {"x": 120, "y": 80, "width": 0, "height": 20}},
        ]

        for scale in (1.0, 1.25, 2.0):
            with self.subTest(scale=scale):
                w, h = int(300 * scale), int(200 * scale)
                base = bytes((15, 20, 30, 255)) * (w * h)
                buf = bytearray(base)
                count = png_overlay.render_som_annotations(
                    buf,
                    w,
                    h,
                    elements,
                    origin_x=100.0,
                    origin_y=50.0,
                    scale_x=scale,
                    scale_y=scale,
                )
                self.assertEqual(count, 3)
                self.assertNotEqual(bytes(buf), base)

        # Tiny canvas edge-clipping stress test (must never raise IndexError/ValueError)
        tiny_buf = bytearray(bytes((0, 0, 0, 255)) * (12 * 12))
        rendered = png_overlay.render_som_annotations(
            tiny_buf,
            12,
            12,
            [{"label": "e999", "bounds": {"x": -5, "y": -5, "width": 25, "height": 25}}],
            scale_x=2.0,
            scale_y=2.0,
        )
        self.assertEqual(rendered, 1)

    def test_roundtrip_write_annotate_read_png(self) -> None:
        w, h = 250, 160
        orig = bytearray(bytes((240, 244, 248, 255)) * (w * h))
        src_path = self.tmp_dir / "src.png"
        out_path = self.tmp_dir / "annotated.png"
        png_overlay.write_png(src_path, w, h, orig)

        rw, rh, decoded = png_overlay.read_png(src_path)
        self.assertEqual((rw, rh), (w, h))
        self.assertEqual(decoded, orig)

        meta = png_overlay.annotate_png(
            src_path,
            out_path,
            elements=[
                {"label": "e1", "bounds": {"x": 20, "y": 20, "width": 90, "height": 32}},
                {"label": "e2", "bounds": {"x": 130, "y": 70, "width": 100, "height": 40}},
            ],
            draw_grid=True,
            scale_x=1.0,
            scale_y=1.0,
        )
        self.assertEqual(meta["width"], w)
        self.assertEqual(meta["height"], h)
        self.assertEqual(meta["rendered_elements"], 2)
        self.assertTrue(meta["grid"])

        aw, ah, annotated = png_overlay.read_png(out_path)
        self.assertEqual((aw, ah), (w, h))
        self.assertEqual(len(annotated), w * h * 4)
        self.assertNotEqual(annotated, orig)


# ---------------------------------------------------------------------------
# 2. pilot_ctl.py Risk-Guard & Audit Trace tests
# ---------------------------------------------------------------------------


class TestRiskGuardAndTracing(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_risky_keywords_english_and_swedish_and_force_quit(self) -> None:
        risky_cases = [
            ("press", "Send Email", "send"),
            ("click", "Skicka mejl", "skicka"),
            ("click", "Publish article", "publish"),
            ("click", "Publicera inlägg", "publicera"),
            ("click", "Delete account", "delete"),
            ("click", "Radera dokument", "radera"),
            ("click", "Ta bort filen", "ta bort"),
            ("click", "Empty Trash", "empty trash"),
            ("click", "Töm papperskorgen", "töm papperskorgen"),
            ("click", "Betala faktura", "betala"),
            ("click", "Köp biljett", "köp"),
            ("click", "Överför pengar", "överför"),
            ("click", "Signera avtal", "signera"),
            ("click", "Confirm Payment now", "confirm payment"),
            ("click", "Submit Order", "submit order"),
            ("quit --force", "Finder", "quit --force"),
            ("close-app --force", "Chrome", "close-app --force"),
            ("safe-close --force", "Mousepad", "safe-close --force"),
        ]
        for action, target, expected_kw in risky_cases:
            with self.subTest(action=action, target=target):
                res = pilot_ctl.evaluate_risk_guard(action, target)
                self.assertIsNotNone(res)
                assert res is not None
                self.assertEqual(res["code"], "RISK_GUARD_BLOCKED")
                self.assertEqual(res["matched_keyword"], expected_kw)

    def test_unicode_word_boundaries_no_false_positives(self) -> None:
        safe_targets = [
            "Beskicka",
            "sender",
            "display",
            "Repayable",
            "Postnummer",
            "Signerad kopia",
            "Skickade meddelanden",
            "Formatera text",
            "Open Settings",
            "Spara utkast",
        ]
        for target in safe_targets:
            with self.subTest(target=target):
                self.assertIsNone(pilot_ctl.evaluate_risk_guard("click", target))

    def test_guard_check_exit_2_and_confirm_risky_before_and_after(self) -> None:
        # Blocked without --confirm-risky -> exit 2 + JSON RISK_GUARD_BLOCKED
        rc, out, _err = _capture_pilot_main(["guard-check", "press", "Skicka mejl"])
        self.assertEqual(rc, 2)
        payload = json.loads(out)
        self.assertEqual(payload["code"], "RISK_GUARD_BLOCKED")
        self.assertEqual(payload["matched_keyword"], "skicka")

        # Allowed with --confirm-risky BEFORE subcommand
        rc_before, out_before, _ = _capture_pilot_main(
            ["--confirm-risky", "guard-check", "press", "Skicka mejl", "--json"]
        )
        self.assertEqual(rc_before, 0)
        p_before = json.loads(out_before)
        self.assertTrue(p_before["ok"])
        self.assertTrue(p_before["confirmed"])
        self.assertFalse(p_before["risk_blocked"])

        # Allowed with --confirm-risky AFTER subcommand
        rc_after, out_after, _ = _capture_pilot_main(
            ["guard-check", "press", "Skicka mejl", "--json", "--confirm-risky"]
        )
        self.assertEqual(rc_after, 0)
        p_after = json.loads(out_after)
        self.assertTrue(p_after["ok"])
        self.assertTrue(p_after["confirmed"])

    def test_safe_close_force_and_safe_click_guard(self) -> None:
        with mock.patch.object(
            pilot_ctl,
            "run_agent_desktop",
            return_value=(0, {"ok": True}, '{"ok":true}', ""),
        ) as mock_ad:
            # Graceful safe-close works without --confirm-risky
            rc, _, _ = _capture_pilot_main(["safe-close", "Notes", "--json"])
            self.assertEqual(rc, 0)
            mock_ad.assert_called_once()

            mock_ad.reset_mock()
            # Force close blocked with exit 2 and never invokes agent-desktop
            rc_blk, out_blk, _ = _capture_pilot_main(["safe-close", "Notes", "--force"])
            self.assertEqual(rc_blk, 2)
            self.assertEqual(json.loads(out_blk)["code"], "RISK_GUARD_BLOCKED")
            mock_ad.assert_not_called()

            # Force close allowed with --confirm-risky
            rc_ok, _, _ = _capture_pilot_main(["safe-close", "Notes", "--force", "--confirm-risky", "--json"])
            self.assertEqual(rc_ok, 0)
            self.assertIn("--force", mock_ad.call_args[0][0])

            mock_ad.reset_mock()
            # safe-click blocked on risky label
            rc_sc, out_sc, _ = _capture_pilot_main(["safe-click", "@s1:e5", "--label", "Ta bort konto"])
            self.assertEqual(rc_sc, 2)
            self.assertEqual(json.loads(out_sc)["code"], "RISK_GUARD_BLOCKED")
            mock_ad.assert_not_called()

    def test_trace_flag_and_env_jsonl(self) -> None:
        trace_file = self.tmp_dir / "audit.jsonl"

        # 1. --trace before subcommand (blocked -> exit 2)
        rc1, _, _ = _capture_pilot_main(["--trace", str(trace_file), "guard-check", "press", "Delete file"])
        self.assertEqual(rc1, 2)

        # 2. --trace= after subcommand (safe -> exit 0)
        rc2, _, _ = _capture_pilot_main(["guard-check", "click", "Beskicka", f"--trace={trace_file}"])
        self.assertEqual(rc2, 0)

        # 3. PILOT_CTL_TRACE env var
        with mock.patch.dict(os.environ, {"PILOT_CTL_TRACE": str(trace_file)}):
            rc3, _, _ = _capture_pilot_main(["guard-check", "click", "Confirm Payment", "--confirm-risky"])
            self.assertEqual(rc3, 0)

        lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)
        entries = [json.loads(line) for line in lines]

        self.assertTrue(entries[0]["risk_blocked"])
        self.assertEqual(entries[0]["exit_code"], 2)
        self.assertEqual(entries[0]["command"], "guard-check")
        self.assertIn("timestamp", entries[0])
        self.assertIn("duration_ms", entries[0])

        self.assertFalse(entries[1]["risk_blocked"])
        self.assertEqual(entries[1]["exit_code"], 0)

        self.assertFalse(entries[2]["risk_blocked"])
        self.assertEqual(entries[2]["exit_code"], 0)


# ---------------------------------------------------------------------------
# 3. pilot_ctl.py window-tile tests
# ---------------------------------------------------------------------------


class TestWindowTile(unittest.TestCase):
    def test_all_six_positions_and_platform_menubar_margin(self) -> None:
        primary = {
            "id": "disp-1",
            "is_primary": True,
            "bounds": {"x": 0, "y": 0, "width": 1200, "height": 800},
        }

        # Linux / Windows: y=0, full 800px height
        for plat in ("linux", "win32"):
            with mock.patch.object(sys, "platform", plat):
                self.assertEqual(pilot_ctl.compute_tile_rect(primary, "left"), (0, 0, 600, 800))
                self.assertEqual(pilot_ctl.compute_tile_rect(primary, "right"), (600, 0, 600, 800))
                self.assertEqual(pilot_ctl.compute_tile_rect(primary, "top"), (0, 0, 1200, 400))
                self.assertEqual(pilot_ctl.compute_tile_rect(primary, "bottom"), (0, 400, 1200, 400))
                self.assertEqual(pilot_ctl.compute_tile_rect(primary, "maximize"), (0, 0, 1200, 800))
                self.assertEqual(pilot_ctl.compute_tile_rect(primary, "center"), (180, 100, 840, 600))

        # macOS primary display: reserves 25pt top menu-bar margin -> y=25, h=775
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(pilot_ctl.compute_tile_rect(primary, "left"), (0, 25, 600, 775))
            self.assertEqual(pilot_ctl.compute_tile_rect(primary, "right"), (600, 25, 600, 775))
            self.assertEqual(pilot_ctl.compute_tile_rect(primary, "top"), (0, 25, 1200, 388))
            self.assertEqual(pilot_ctl.compute_tile_rect(primary, "bottom"), (0, 412, 1200, 388))
            self.assertEqual(pilot_ctl.compute_tile_rect(primary, "maximize"), (0, 25, 1200, 775))
            cx, cy, cw, ch = pilot_ctl.compute_tile_rect(primary, "center")
            self.assertEqual((cw, ch), (840, 581))
            self.assertEqual(cx, 180)
            self.assertEqual(cy, 122)

    def test_multi_display_and_negative_coordinates_with_equals_syntax(self) -> None:
        displays = [
            {"id": "1", "is_primary": True, "bounds": {"x": 0, "y": 0, "width": 1710, "height": 1112}},
            {"id": "2", "is_primary": False, "bounds": {"x": -1920, "y": -120, "width": 1920, "height": 1080}},
        ]
        windows = [
            {"id": "w-42", "title": "Editor", "visible": True, "minimized": False, "is_focused": True},
        ]
        recorded_calls: List[List[str]] = []

        def fake_ad(args: Sequence[str], **_kwargs: Any) -> Tuple[int, Optional[Dict[str, Any]], str, str]:
            cmd = list(args)
            recorded_calls.append(cmd)
            if cmd[0] == "list-displays":
                return 0, {"ok": True, "data": displays}, "", ""
            if cmd[0] == "list-windows":
                return 0, {"ok": True, "data": windows}, "", ""
            return 0, {"ok": True, "data": {}}, '{"ok":true}', ""

        with mock.patch.object(pilot_ctl, "run_agent_desktop", side_effect=fake_ad):
            rc, out, _ = _capture_pilot_main(
                ["window-tile", "Code", "left", "--display", "2", "--json"]
            )

        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["display_id"], "2")
        self.assertEqual(
            payload["target_bounds"],
            {"x": -1920, "y": -120, "width": 960, "height": 1080},
        )

        move_calls = [c for c in recorded_calls if c[0] == "move-window"]
        resize_calls = [c for c in recorded_calls if c[0] == "resize-window"]
        self.assertEqual(len(move_calls), 1)
        self.assertEqual(len(resize_calls), 1)
        # Critical: negative x/y MUST use '=' syntax (--x=-1920, --y=-120)
        self.assertIn("--x=-1920", move_calls[0])
        self.assertIn("--y=-120", move_calls[0])
        self.assertIn("--window-id", move_calls[0])
        self.assertIn("w-42", move_calls[0])
        self.assertIn("--width", resize_calls[0])
        self.assertIn("960", resize_calls[0])
        self.assertIn("--height", resize_calls[0])
        self.assertIn("1080", resize_calls[0])


# ---------------------------------------------------------------------------
# 4. pilot_ctl.py screenshot offscreen fallback (Linux X11) tests
# ---------------------------------------------------------------------------


class TestScreenshotOffscreenFallback(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_offscreen_window_falls_back_to_display_crop_with_som(self) -> None:
        out_png = self.tmp_dir / "linux-som.png"
        displays = [
            {"id": "screen", "is_primary": True, "scale": 1.0, "bounds": {"x": 0.0, "y": 0.0, "width": 1280.0, "height": 800.0}}
        ]
        windows = [
            {
                "id": "w-linux-1",
                "title": "Untitled 1 - Mousepad",
                "visible": True,
                "minimized": False,
                "is_focused": True,
                "bounds": {"x": -1.0, "y": -20.0, "width": 642.0, "height": 825.0},
            }
        ]
        snapshot_tree = {
            "bounds": {"x": -1.0, "y": -20.0, "width": 642.0, "height": 825.0},
            "children": [
                {
                    "ref_id": "@s1:e1",
                    "role": "menu",
                    "name": "File",
                    "bounds": {"x": 0.0, "y": 0.0, "width": 39.0, "height": 25.0},
                },
                {
                    "ref_id": "@s1:e2",
                    "role": "textfield",
                    "name": "Document",
                    "bounds": {"x": 1.0, "y": 28.0, "width": 638.0, "height": 769.0},
                },
            ],
        }

        def fake_ad(args: Sequence[str], **_kwargs: Any) -> Tuple[int, Optional[Dict[str, Any]], str, str]:
            cmd = list(args)
            if cmd[0] == "list-displays":
                return 0, {"ok": True, "data": displays}, "", ""
            if cmd[0] == "list-windows":
                return 0, {"ok": True, "data": windows}, "", ""
            if cmd[0] == "screenshot" and "--app" in cmd:
                return (
                    1,
                    {"ok": False, "error": {"code": "INTERNAL", "message": "The window is not fully on the screen"}},
                    "",
                    "The window is not fully on the screen",
                )
            if cmd[0] == "screenshot" and "--screen" in cmd:
                target_path = Path(cmd[-1])
                buf = bytearray(bytes((25, 30, 35, 255)) * (1280 * 800))
                png_overlay.write_png(target_path, 1280, 800, buf)
                return 0, {"ok": True, "data": {"width": 1280, "height": 800, "scale_factor": 1.0}}, "", ""
            if cmd[0] == "snapshot":
                return 0, {"ok": True, "data": {"snapshot_id": "s1", "tree": snapshot_tree}}, "", ""
            return 1, {"ok": False}, "", "unexpected"

        with mock.patch.object(pilot_ctl, "run_agent_desktop", side_effect=fake_ad), mock.patch.object(
            pilot_ctl, "get_refs_cache_path", return_value=self.tmp_dir / "refs.json"
        ):
            rc, out, _ = _capture_pilot_main(
                ["screenshot", str(out_png), "--app", "mousepad", "--annotate", "--grid", "--json"]
            )

        self.assertEqual(rc, 0)
        res = json.loads(out)
        self.assertTrue(res["ok"])
        self.assertTrue(res["screen_crop_fallback"])
        self.assertEqual(res["width"], 641)
        self.assertEqual(res["height"], 800)
        self.assertEqual(res["rendered_elements"], 2)
        rw, rh, _buf = png_overlay.read_png(out_png)
        self.assertEqual((rw, rh), (641, 800))


# ---------------------------------------------------------------------------
# 5. pilot_ctl.py Recipe lifecycle & safety tests
# ---------------------------------------------------------------------------


class TestRecipes(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.recipes_dir = Path(self._tmp.name) / "recipes"
        self.env_patch = mock.patch.dict(
            os.environ, {"ANTIGRAVITY_PILOT_RECIPES_DIR": str(self.recipes_dir)}
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self._tmp.cleanup()

    def test_path_traversal_rejected(self) -> None:
        bad_names = ["../escape", "a/b", r"a\b", "..", ".hidden", "has space", ""]
        sample_json = json.dumps({"steps": [{"action": "wait", "seconds": 0}]})
        for bad in bad_names:
            with self.subTest(name=bad):
                rc, _out, err = _capture_pilot_main(["recipe", "save", bad, "--stdin"], stdin_text=sample_json)
                self.assertEqual(rc, 1)
                self.assertIn("INVALID_RECIPE_NAME", err)
        self.assertFalse((Path(self._tmp.name) / "escape.json").exists())

    def test_save_list_show_dry_run_missing_param_run_and_delete(self) -> None:
        recipe_obj = {
            "description": "Draft and optionally send note",
            "steps": [
                {"action": "launch", "app": "{{app}}"},
                {"action": "set-value", "ref": "@s1:e1", "value": "{{subject}}"},
                {"action": "type", "ref": "@s1:e2", "text": "{{body}}"},
                {"action": "click", "x": 120, "y": 340},
                {"action": "click", "ref": "@s1:e9", "label": "Skicka {{subject}}"},
                {"action": "key", "combo": "escape"},
            ],
        }

        # 1. save --stdin
        rc, out, _ = _capture_pilot_main(
            ["recipe", "save", "daily-note", "--stdin"],
            stdin_text=json.dumps(recipe_obj),
        )
        self.assertEqual(rc, 0)
        self.assertIn("Saved recipe 'daily-note'", out)

        # 2. list --json
        rc, out, _ = _capture_pilot_main(["recipe", "list", "--json"])
        self.assertEqual(rc, 0)
        listed = json.loads(out)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], "daily-note")
        self.assertEqual(listed[0]["step_count"], 6)
        self.assertEqual(listed[0]["params"], ["app", "subject", "body"])

        # 3. show
        rc, out, _ = _capture_pilot_main(["recipe", "show", "daily-note"])
        self.assertEqual(rc, 0)
        shown = json.loads(out)
        self.assertEqual(shown["description"], "Draft and optionally send note")

        # 4. missing parameter -> exit 1 + MISSING_PARAMS
        rc, out, _ = _capture_pilot_main(
            ["recipe", "run", "daily-note", "--param", "app=Notes", "--json"]
        )
        self.assertEqual(rc, 1)
        miss = json.loads(out)
        self.assertEqual(miss["code"], "MISSING_PARAMS")
        self.assertEqual(miss["missing_params"], ["body", "subject"])

        # 5. --dry-run flags risky step without running agent-desktop
        with mock.patch.object(pilot_ctl, "run_agent_desktop") as mock_ad:
            rc, out, _ = _capture_pilot_main(
                [
                    "recipe",
                    "run",
                    "daily-note",
                    "--param",
                    "app=Notes",
                    "--param",
                    "subject=Status",
                    "--param",
                    "body=All green",
                    "--dry-run",
                    "--json",
                ]
            )
            self.assertEqual(rc, 0)
            mock_ad.assert_not_called()
            dry = json.loads(out)
            self.assertTrue(dry["dry_run"])
            self.assertEqual(dry["steps"][0]["step"]["app"], "Notes")
            self.assertEqual(dry["steps"][4]["step"]["label"], "Skicka Status")
            self.assertTrue(dry["steps"][4]["would_block"])
            self.assertEqual(dry["steps"][4]["risk_guard"]["matched_keyword"], "skicka")

        # 6. Execute recipe against mocked agent-desktop: stops mid-recipe at step 5 (exit 2)
        invocations: List[Tuple[List[str], Optional[str], bool]] = []

        def fake_ad(
            args: Sequence[str],
            *,
            headed: bool = False,
            input_text: Optional[str] = None,
            **_kwargs: Any,
        ) -> Tuple[int, Optional[Dict[str, Any]], str, str]:
            invocations.append((list(args), input_text, headed))
            return 0, {"ok": True, "data": {}}, '{"ok":true}', ""

        with mock.patch.object(pilot_ctl, "run_agent_desktop", side_effect=fake_ad):
            rc, out, _ = _capture_pilot_main(
                [
                    "recipe",
                    "run",
                    "daily-note",
                    "--param",
                    "app=Notes",
                    "--param",
                    "subject=Status",
                    "--param",
                    "body=Secret payload",
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("RISK_GUARD_BLOCKED", out)
        # Steps 1..4 ran; steps 5 & 6 were blocked before execution
        self.assertEqual(len(invocations), 4)
        self.assertEqual(invocations[0][0], ["launch", "Notes", "--activate"])
        self.assertEqual(invocations[1][0], ["set-value", "@s1:e1", "--stdin"])
        self.assertEqual(invocations[1][1], "Status")
        self.assertEqual(invocations[2][0], ["type", "@s1:e2", "--stdin"])
        self.assertEqual(invocations[2][1], "Secret payload")
        # Regression check: mouse-click MUST use --xy=x,y and headed=True
        self.assertEqual(invocations[3][0], ["mouse-click", "--xy=120,340"])
        self.assertTrue(invocations[3][2])

        # 7. With --confirm-risky, all 6 steps complete
        invocations.clear()
        with mock.patch.object(pilot_ctl, "run_agent_desktop", side_effect=fake_ad):
            rc, out, _ = _capture_pilot_main(
                [
                    "recipe",
                    "run",
                    "daily-note",
                    "--param",
                    "app=Notes",
                    "--param",
                    "subject=Status",
                    "--param",
                    "body=Secret payload",
                    "--confirm-risky",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        run_res = json.loads(out)
        self.assertTrue(run_res["ok"])
        self.assertEqual(run_res["step_count"], 6)
        self.assertEqual(invocations[-1][0], ["press", "escape"])

        # 8. delete
        rc, out, _ = _capture_pilot_main(["recipe", "delete", "daily-note"])
        self.assertEqual(rc, 0)
        self.assertIn("Deleted recipe 'daily-note'", out)
        rc_missing, _, _ = _capture_pilot_main(["recipe", "show", "daily-note"])
        self.assertEqual(rc_missing, 1)


# ---------------------------------------------------------------------------
# 6. Windows platform simulation (sys.platform="win32", os.name="nt")
# ---------------------------------------------------------------------------


class TestWindowsPlatformSimulation(unittest.TestCase):
    def test_windows_recipes_dir_appdata(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "ANTIGRAVITY_PILOT_RECIPES_DIR"}
        env["APPDATA"] = r"C:\Users\alice\AppData\Roaming"
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(pilot_ctl, "Path", _FakeWindowsPath),
            mock.patch.dict(os.environ, env, clear=True),
        ):
            rdir = pilot_ctl.get_recipes_dir()
            self.assertEqual(str(rdir), r"C:\Users\alice\AppData\Roaming\antigravity-pilot\recipes")

        # Fallback to USERPROFILE\AppData\Roaming when APPDATA is unset
        env.pop("APPDATA", None)
        env["USERPROFILE"] = r"C:\Users\bob"
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(pilot_ctl, "Path", _FakeWindowsPath),
            mock.patch.dict(os.environ, env, clear=True),
        ):
            rdir2 = pilot_ctl.get_recipes_dir()
            self.assertEqual(str(rdir2), r"C:\Users\bob\AppData\Roaming\antigravity-pilot\recipes")

    def test_windows_agent_desktop_exe_and_cmd_lookup(self) -> None:
        queried: List[str] = []

        def fake_which(name: str) -> Optional[str]:
            queried.append(name)
            if name == "agent-desktop.cmd":
                return r"C:\Users\alice\AppData\Roaming\npm\agent-desktop.cmd"
            return None

        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(pilot_ctl.shutil, "which", side_effect=fake_which),
        ):
            found = pilot_ctl.find_agent_desktop()
        self.assertEqual(found, r"C:\Users\alice\AppData\Roaming\npm\agent-desktop.cmd")
        self.assertEqual(queried[:2], ["agent-desktop.exe", "agent-desktop.cmd"])

        # .exe takes precedence when both exist
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(
                pilot_ctl.shutil,
                "which",
                side_effect=lambda n: r"C:\tools\agent-desktop.exe" if n == "agent-desktop.exe" else None,
            ),
        ):
            self.assertEqual(pilot_ctl.find_agent_desktop(), r"C:\tools\agent-desktop.exe")

    def test_windows_notify_powershell(self) -> None:
        captured: Dict[str, Any] = {}

        def fake_run(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env") or {}
            return subprocess.CompletedProcess(list(cmd), 0, stdout="", stderr="")

        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(os, "name", "nt"),
            mock.patch.object(
                pilot_ctl.shutil,
                "which",
                side_effect=lambda n: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
                if n == "powershell"
                else None,
            ),
            mock.patch.object(pilot_ctl, "run_subprocess", side_effect=fake_run),
        ):
            rc, out, _ = _capture_pilot_main(["notify", "Build OK", "All tests passed", "--json"])

        self.assertEqual(rc, 0)
        res = json.loads(out)
        self.assertEqual(res["backend"], "powershell")
        self.assertIn("-NoProfile", captured["cmd"])
        self.assertIn("-Command", captured["cmd"])
        self.assertEqual(captured["env"]["AG_PILOT_NOTIFY_TITLE"], "Build OK")
        self.assertEqual(captured["env"]["AG_PILOT_NOTIFY_MSG"], "All tests passed")

    def test_windows_record_and_shortcut_unsupported_platform(self) -> None:
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(os, "name", "nt"),
        ):
            rc_rec, out_rec, _ = _capture_pilot_main(["record"])
            self.assertEqual(rc_rec, 1)
            p_rec = json.loads(out_rec)
            self.assertFalse(p_rec["ok"])
            self.assertEqual(p_rec["code"], "UNSUPPORTED_PLATFORM")
            self.assertEqual(p_rec["platform"], "win32")
            self.assertEqual(p_rec["command"], "record")

            rc_sc, out_sc, _ = _capture_pilot_main(["shortcut", "Morning Brief"])
            self.assertEqual(rc_sc, 1)
            p_sc = json.loads(out_sc)
            self.assertFalse(p_sc["ok"])
            self.assertEqual(p_sc["code"], "UNSUPPORTED_PLATFORM")
            self.assertEqual(p_sc["platform"], "win32")
            self.assertEqual(p_sc["command"], "shortcut")


# ---------------------------------------------------------------------------
# 7. Linux platform simulation (sys.platform="linux")
# ---------------------------------------------------------------------------


class TestLinuxPlatformSimulation(unittest.TestCase):
    def test_linux_xdg_config_home_recipes_dir(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "ANTIGRAVITY_PILOT_RECIPES_DIR"}
        env["XDG_CONFIG_HOME"] = "/tmp/xdg-cfg"
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(os, "name", "posix"),
            mock.patch.dict(os.environ, env, clear=True),
        ):
            self.assertEqual(
                pilot_ctl.get_recipes_dir(),
                Path("/tmp/xdg-cfg/antigravity-pilot/recipes"),
            )

        env.pop("XDG_CONFIG_HOME", None)
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(os, "name", "posix"),
            mock.patch.dict(os.environ, env, clear=True),
        ):
            self.assertEqual(
                pilot_ctl.get_recipes_dir(),
                Path.home() / ".config" / "antigravity-pilot" / "recipes",
            )

    def test_linux_notify_with_and_without_notify_send(self) -> None:
        # With notify-send available
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(os, "name", "posix"),
            mock.patch.object(
                pilot_ctl.shutil,
                "which",
                side_effect=lambda n: "/usr/bin/notify-send" if n == "notify-send" else None,
            ),
            mock.patch.object(
                pilot_ctl,
                "run_subprocess",
                return_value=subprocess.CompletedProcess(["/usr/bin/notify-send"], 0, "", ""),
            ) as mock_run,
        ):
            rc, out, _ = _capture_pilot_main(["notify", "Pilot", "Ready", "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["backend"], "notify-send")
            self.assertEqual(mock_run.call_args[0][0], ["/usr/bin/notify-send", "Pilot", "Ready"])

        # Without notify-send -> clean stdout fallback
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(os, "name", "posix"),
            mock.patch.object(pilot_ctl.shutil, "which", return_value=None),
        ):
            rc2, out2, _ = _capture_pilot_main(["notify", "Pilot", "Headless", "--json"])
            self.assertEqual(rc2, 0)
            self.assertEqual(json.loads(out2)["backend"], "stdout")

    def test_linux_record_and_shortcut_unsupported_platform(self) -> None:
        with (
            mock.patch.object(sys, "platform", "linux"),
            mock.patch.object(os, "name", "posix"),
        ):
            rc_rec, out_rec, _ = _capture_pilot_main(["record"])
            self.assertEqual(rc_rec, 1)
            p_rec = json.loads(out_rec)
            self.assertFalse(p_rec["ok"])
            self.assertEqual(p_rec["code"], "UNSUPPORTED_PLATFORM")
            self.assertEqual(p_rec["platform"], "linux")

            rc_sc, out_sc, _ = _capture_pilot_main(["shortcut", "Daily"])
            self.assertEqual(rc_sc, 1)
            p_sc = json.loads(out_sc)
            self.assertFalse(p_sc["ok"])
            self.assertEqual(p_sc["code"], "UNSUPPORTED_PLATFORM")
            self.assertEqual(p_sc["platform"], "linux")


# ---------------------------------------------------------------------------
# 8. sandbox_ctl.py tests
# ---------------------------------------------------------------------------


class TestSandboxCtl(unittest.TestCase):
    def test_name_and_port_validation(self) -> None:
        for valid in ("a", "dev", "worker-1", "qa_02", "a" * 48):
            self.assertEqual(sandbox_ctl.validate_name(valid), valid)

        for invalid in ("", "-bad", "_bad", "Upper", "../x", "a/b", "a.b", "a b", "a" * 49):
            with self.subTest(invalid=invalid):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        sandbox_ctl.validate_name(invalid)
                self.assertEqual(ctx.exception.code, 2)

        self.assertEqual(sandbox_ctl.validate_port(6080), 6080)
        for bad_port in (0, -5, 65536, "not-a-port"):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    sandbox_ctl.validate_port(bad_port)
            self.assertEqual(ctx.exception.code, 2)

    def test_free_port_allocation_on_loopback(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied_port = sock.getsockname()[1]
        try:
            self.assertFalse(sandbox_ctl.is_port_free(occupied_port))
            claimed_next = occupied_port + 1
            with mock.patch.object(sandbox_ctl, "used_sandbox_vnc_ports", return_value={claimed_next}):
                chosen = sandbox_ctl.find_free_vnc_port(start=occupied_port, end=occupied_port + 10)
            self.assertNotEqual(chosen, occupied_port)
            self.assertNotEqual(chosen, claimed_next)
            self.assertGreaterEqual(chosen, occupied_port + 2)
            self.assertTrue(sandbox_ctl.is_port_free(chosen))
        finally:
            sock.close()

    def test_list_json_parsing_and_url_formatting(self) -> None:
        self.assertEqual(
            sandbox_ctl.novnc_url(6082),
            "http://127.0.0.1:6082/vnc.html?autoconnect=1&resize=scale",
        )

        ps_stdout = (
            "ag-desk-alpha\tUp 3 minutes\tantigravity-desktop-sandbox:latest\t3 minutes ago\t6080\n"
            "ag-desk-beta\tUp 1 minute\tantigravity-desktop-sandbox:latest\t1 minute ago\t\n"
            "other-container\tUp 5 minutes\tnginx:latest\t5 minutes ago\t\n"
        )

        def fake_run_docker(args: Sequence[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args[0] == "info":
                return subprocess.CompletedProcess(["docker", *args], 0, stdout="27.0.0\n", stderr="")
            if args[0] == "ps":
                return subprocess.CompletedProcess(["docker", *args], 0, stdout=ps_stdout, stderr="")
            if args[0] == "inspect":
                info = {
                    "State": {"Running": True},
                    "Config": {"Labels": {"antigravity.sandbox.vnc_port": "6080"}},
                }
                return subprocess.CompletedProcess(["docker", *args], 0, stdout=json.dumps(info), stderr="")
            return subprocess.CompletedProcess(["docker", *args], 1, stdout="", stderr="unexpected")

        with (
            mock.patch.object(sandbox_ctl.shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(sandbox_ctl, "run_docker", side_effect=fake_run_docker),
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sandbox_ctl.main(["list", "--json"])
            self.assertEqual(rc, 0)
            rows = json.loads(buf.getvalue())
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["name"], "alpha")
            self.assertEqual(rows[0]["vnc_port"], 6080)
            self.assertEqual(
                rows[0]["novnc_url"],
                "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale",
            )
            self.assertEqual(rows[1]["name"], "beta")
            self.assertIsNone(rows[1]["vnc_port"])
            self.assertIsNone(rows[1]["novnc_url"])

            url_buf = io.StringIO()
            with contextlib.redirect_stdout(url_buf):
                rc_url = sandbox_ctl.main(["url", "alpha"])
            self.assertEqual(rc_url, 0)
            self.assertEqual(
                url_buf.getvalue().strip(),
                "http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale",
            )

    def test_missing_docker_error_handling(self) -> None:
        err_buf = io.StringIO()
        with (
            mock.patch.object(sandbox_ctl.shutil, "which", return_value=None),
            contextlib.redirect_stderr(err_buf),
        ):
            with self.assertRaises(SystemExit) as ctx:
                sandbox_ctl.ensure_docker()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("not found on PATH", err_buf.getvalue())

        # Daemon stopped error
        err_buf2 = io.StringIO()
        with (
            mock.patch.object(sandbox_ctl.shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(
                sandbox_ctl,
                "run_docker",
                return_value=subprocess.CompletedProcess(["docker", "info"], 1, "", "Cannot connect to daemon"),
            ),
            contextlib.redirect_stderr(err_buf2),
        ):
            with self.assertRaises(SystemExit) as ctx2:
                sandbox_ctl.ensure_docker()
        self.assertEqual(ctx2.exception.code, 1)
        self.assertIn("not responding", err_buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
