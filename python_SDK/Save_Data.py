#!/usr/bin/env python3
"""
ironbci16_record.py  -- record N samples to an Excel file (fast)
================================================================
IRONBCI_16 (STM32WB55 + 2x ADS1299) BLE reader that grabs a fixed number
of samples and writes them to a .xlsx with 16 columns (ch1 .. ch16).

Prints a timing breakdown (scan / connect / stream / write) and the
effective sample rate, so you can see where the time actually goes.

Install (Python 3.8+):
    py -m pip install bleak numpy openpyxl

Run from cmd:
    py ironbci16_record.py --samples 2500
    py ironbci16_record.py --address 00:80:E1:26:68:0D --samples 2500   # skips scan
    py ironbci16_record.py --out session1.xlsx
    py ironbci16_record.py --raw
"""

import argparse
import asyncio
import sys
import time
import traceback
from typing import Optional

import numpy as np

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("Missing bleak. Run:  py -m pip install bleak")

try:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
except ImportError:
    sys.exit("Missing openpyxl. Run:  py -m pip install openpyxl")

# ---------------------------------------------------------------- protocol --
DEVICE_NAME  = "IRONBCI_16"
NOTIFY_UUID  = "0000fe42-8e22-4541-9d4c-21edae82ed19"

N_CHANNELS   = 16
BYTES_PER_CH = 3
SAMPLE_RATE  = 250.0                         # SPS (firmware CONFIG1 = 0x96)
VREF, GAIN   = 4.5, 1.0
UV_PER_LSB   = (VREF / GAIN) / (2 ** 23) * 1e6   # ~0.536 uV per count

CONNECT_TIMEOUT = 20.0
CONNECT_RETRIES = 4


def parse_packet(payload: bytes, to_uv: bool = True) -> np.ndarray:
    usable = (len(payload) // (N_CHANNELS * BYTES_PER_CH)) * N_CHANNELS * BYTES_PER_CH
    if usable == 0:
        return np.empty((0, N_CHANNELS))
    raw = np.frombuffer(payload[:usable], dtype=np.uint8).reshape(-1, 3)
    v = (raw[:, 0].astype(np.int32) << 16) | (raw[:, 1].astype(np.int32) << 8) | raw[:, 2]
    v = np.where(v & 0x800000, v - 0x1000000, v)
    v = v.reshape(-1, N_CHANNELS).astype(np.float64)
    return v * UV_PER_LSB if to_uv else v


# --------------------------------------------------------------- recording --
async def _record_once(target, n_samples: int, to_uv: bool, timings: dict) -> np.ndarray:
    chunks = []
    collected = 0
    last_report = 0
    t = {"first": None, "last": None}
    done = asyncio.Event()

    def _handle(_c, data: bytearray):
        nonlocal collected, last_report
        if done.is_set():
            return
        now = time.perf_counter()
        if t["first"] is None:
            t["first"] = now
        t["last"] = now
        s = parse_packet(bytes(data), to_uv=to_uv)
        if s.size == 0:
            return
        chunks.append(s)
        collected += s.shape[0]
        if collected - last_report >= 250:
            print(f"  {min(collected, n_samples)} / {n_samples} samples", end="\r")
            last_report = collected
        if collected >= n_samples:
            done.set()

    def _on_disconnect(_c):
        if not done.is_set():
            done.set()

    t_connect_start = time.perf_counter()
    async with BleakClient(target, timeout=CONNECT_TIMEOUT,
                           disconnected_callback=_on_disconnect) as client:
        await client.start_notify(NOTIFY_UUID, _handle)
        timings["connect"] = time.perf_counter() - t_connect_start
        print("Connected. Recording...")
        await done.wait()
        try:
            await client.stop_notify(NOTIFY_UUID)
        except Exception:
            pass
    print()

    if not chunks:
        raise RuntimeError("Connected but received no data before the link dropped.")

    # pure streaming stats: first packet -> last packet
    if t["first"] is not None and t["last"] and t["last"] > t["first"]:
        timings["stream"] = t["last"] - t["first"]
        timings["eff_sps"] = (collected - chunks[0].shape[0]) / timings["stream"]
    else:
        timings["stream"] = 0.0
        timings["eff_sps"] = float("nan")

    data = np.vstack(chunks)
    if data.shape[0] < n_samples:
        print(f"WARNING: link dropped early -- only {data.shape[0]} of {n_samples} samples.")
    return data[:n_samples]


async def record(address: Optional[str], n_samples: int, to_uv: bool, timings: dict) -> np.ndarray:
    if address is not None:
        target = address
        timings["scan"] = 0.0
    else:
        print(f"Scanning for '{DEVICE_NAME}' ...")
        t0 = time.perf_counter()
        target = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15.0)
        timings["scan"] = time.perf_counter() - t0
        if target is None:
            raise RuntimeError(f"'{DEVICE_NAME}' not found. Board on and not "
                               f"connected to a phone/other app?")
        print(f"Found {target.name} @ {target.address}  "
              f"(tip: pass --address {target.address} next time to skip the scan)")

    last_err = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            return await _record_once(target, n_samples, to_uv, timings)
        except Exception as e:                            # noqa: BLE001
            last_err = e
            print(f"\nAttempt {attempt}/{CONNECT_RETRIES} failed: "
                  f"{type(e).__name__}: {e or '(no message)'}")
            if attempt < CONNECT_RETRIES:
                await asyncio.sleep(2.0)
    raise last_err


# ------------------------------------------------------------------ excel --
def save_xlsx(data: np.ndarray, path: str, to_uv: bool):
    """write_only mode -> streams rows straight to disk, no per-cell styling cost."""
    unit = "uV" if to_uv else "counts"
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("EEG")
    ws.freeze_panes = "A2"

    bold = Font(name="Arial", bold=True)
    header = []
    for i in range(N_CHANNELS):
        c = WriteOnlyCell(ws, value=f"ch{i + 1}")
        c.font = bold
        header.append(c)
    ws.append(header)

    if to_uv:
        rounded = np.round(data, 3)
        for row in rounded.tolist():
            ws.append(row)
    else:
        for row in data.astype(np.int64).tolist():
            ws.append(row)

    wb.save(path)
    print(f"Saved {data.shape[0]} samples x {N_CHANNELS} channels ({unit}) -> {path}")


# -------------------------------------------------------------------- main --
def main():
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8+ required.")

    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=2500)
    ap.add_argument("--out", default="eeg_recording.xlsx")
    ap.add_argument("--address", help="BLE MAC, skips scanning")
    ap.add_argument("--raw", action="store_true", help="store raw ADC counts instead of uV")
    args = ap.parse_args()

    to_uv = not args.raw
    n = args.samples
    print(f"Recording {n} samples (theoretical minimum "
          f"{n / SAMPLE_RATE:.1f} s at {SAMPLE_RATE:.0f} SPS)...")

    timings = {}
    t0 = time.perf_counter()
    try:
        data = asyncio.run(record(args.address, n, to_uv, timings))
    except Exception as e:                                # noqa: BLE001
        print("\n----- FULL ERROR -----")
        traceback.print_exc()
        print("----------------------")
        sys.exit(f"Giving up: {type(e).__name__}: {e or '(no message)'}")

    t_write = time.perf_counter()
    save_xlsx(data, args.out, to_uv)
    timings["write"] = time.perf_counter() - t_write
    total = time.perf_counter() - t0

    print("\n--- timing breakdown ---")
    print(f"  scan     : {timings.get('scan', 0):6.2f} s")
    print(f"  connect  : {timings.get('connect', 0):6.2f} s")
    print(f"  stream   : {timings.get('stream', 0):6.2f} s   "
          f"(effective {timings.get('eff_sps', float('nan')):.1f} SPS)")
    print(f"  write    : {timings.get('write', 0):6.2f} s")
    print(f"  TOTAL    : {total:6.2f} s")
    if timings.get("eff_sps", 0) and timings["eff_sps"] < 0.9 * SAMPLE_RATE:
        print("  NOTE: effective SPS is well below 250 -> the BLE link, not the")
        print("        script, is the bottleneck (connection interval too slow).")


if __name__ == "__main__":
    main()
