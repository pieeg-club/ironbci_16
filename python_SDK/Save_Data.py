#!/usr/bin/env python3
"""
ironbci16_record.py  -- record N samples to an Excel file
=========================================================
IRONBCI_16 (STM32WB55 + 2x ADS1299) BLE reader that grabs a fixed number
of samples and writes them to a .xlsx with 16 columns (ch1 .. ch16).

No plotting, no growing buffer -- it just records until it has enough
samples, then stops and saves.

Install (Python 3.8+):
    py -m pip install bleak numpy openpyxl

Run from cmd:
    py ironbci16_record.py                              # 10000 samples -> eeg_recording.xlsx
    py ironbci16_record.py --samples 5000               # different count
    py ironbci16_record.py --out session1.xlsx          # different file name
    py ironbci16_record.py --address XX:XX:XX:XX:XX:XX   # skip scanning
    py ironbci16_record.py --raw                         # store raw ADC counts, not uV
"""

import argparse
import asyncio
import sys
import time
from typing import Optional

import numpy as np

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("Missing bleak. Run:  py -m pip install bleak")

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font
except ImportError:
    sys.exit("Missing openpyxl. Run:  py -m pip install openpyxl")

# ---------------------------------------------------------------- protocol --
DEVICE_NAME  = "IRONBCI_16"
NOTIFY_UUID  = "0000fe42-8e22-4541-9d4c-21edae82ed19"

N_CHANNELS   = 16
BYTES_PER_CH = 3
SAMPLE_RATE  = 250.0                         # SPS
VREF, GAIN   = 4.5, 1.0
UV_PER_LSB   = (VREF / GAIN) / (2 ** 23) * 1e6   # ~0.536 uV per count


def parse_packet(payload: bytes, to_uv: bool = True) -> np.ndarray:
    """Notification bytes -> (n_samples, 16). Sign-extended 24-bit big-endian."""
    usable = (len(payload) // (N_CHANNELS * BYTES_PER_CH)) * N_CHANNELS * BYTES_PER_CH
    if usable == 0:
        return np.empty((0, N_CHANNELS))
    raw = np.frombuffer(payload[:usable], dtype=np.uint8).reshape(-1, 3)
    v = (raw[:, 0].astype(np.int32) << 16) | (raw[:, 1].astype(np.int32) << 8) | raw[:, 2]
    v = np.where(v & 0x800000, v - 0x1000000, v)          # sign-extend
    v = v.reshape(-1, N_CHANNELS).astype(np.float64)
    return v * UV_PER_LSB if to_uv else v


# --------------------------------------------------------------- recording --
async def record(address: Optional[str], n_samples: int, to_uv: bool) -> np.ndarray:
    if address is None:
        print(f"Scanning for '{DEVICE_NAME}' ...")
        dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15.0)
        if dev is None:
            raise RuntimeError(f"'{DEVICE_NAME}' not found. Board on and not "
                               f"connected to a phone/other app?")
        address = dev.address
        print(f"Found {dev.name} @ {address}")

    chunks = []
    collected = 0
    done = asyncio.Event()
    last_report = 0

    def _handle(_c, data: bytearray):
        nonlocal collected, last_report
        if done.is_set():
            return
        s = parse_packet(bytes(data), to_uv=to_uv)
        if s.size == 0:
            return
        chunks.append(s)
        collected += s.shape[0]
        if collected - last_report >= 250:               # progress ~1x/sec
            #print(f"  {min(collected, n_samples)} / {n_samples} samples", end="\r")
            last_report = collected
        if collected >= n_samples:
            done.set()

    async with BleakClient(address) as client:
        print("Connected. Recording (enabling notifications starts the stream)...")
        await client.start_notify(NOTIFY_UUID, _handle)
        await done.wait()
        try:
            await client.stop_notify(NOTIFY_UUID)
        except Exception:
            pass
    print()  # newline after the \r progress line

    data = np.vstack(chunks)[:n_samples]                 # trim to exactly n_samples
    return data


# ------------------------------------------------------------------ excel --
def save_xlsx(data: np.ndarray, path: str, to_uv: bool):
    unit = "uV" if to_uv else "counts"
    wb = Workbook()
    ws = wb.active
    ws.title = "EEG"

    header_font = Font(name="Arial", bold=True)
    body_font = Font(name="Arial")

    headers = [f"ch{i + 1}" for i in range(N_CHANNELS)]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = header_font

    fmt = "%.3f" if to_uv else "%d"
    for row in data:
        ws.append([float(x) if to_uv else int(x) for x in row])

    # apply body font + a number format down the sheet
    number_format = "0.000" if to_uv else "0"
    for r in range(2, data.shape[0] + 2):
        for c in range(1, N_CHANNELS + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = body_font
            cell.number_format = number_format

    ws.freeze_panes = "A2"                               # keep header visible
    wb.save(path)
    #print(f"Saved {data.shape[0]} samples x {N_CHANNELS} channels ({unit}) -> {path}")


# -------------------------------------------------------------------- main --
def main():
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8+ required. Install from python.org and run:  py script.py")

    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=2500, help="number of samples to record")
    ap.add_argument("--out", default="eeg_recording.xlsx", help="output .xlsx path")
    ap.add_argument("--address", help="BLE MAC, skips scanning (faster startup)")
    ap.add_argument("--raw", action="store_true", help="store raw ADC counts instead of uV")
    args = ap.parse_args()

    to_uv = not args.raw
    n = args.samples
    print(f"Recording {n} samples "
          f"(~{n / SAMPLE_RATE:.0f} s at {SAMPLE_RATE:.0f} SPS)...")

    t0 = time.time()
    try:
        data = asyncio.run(record(args.address, n, to_uv))
    except Exception as e:
        sys.exit(f"Error: {e}")
    print(f"Done in {time.time() - t0:.1f} s. Writing Excel file...")

    save_xlsx(data, args.out, to_uv)


if __name__ == "__main__":
    main()
