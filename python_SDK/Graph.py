#!/usr/bin/env python3
"""
ironbci16_fast.py  -- low-latency Windows version
=================================================
IRONBCI_16 (STM32WB55 + 2x ADS1299) BLE reader + FAST real-time plot.

Why this version is fast / has no growing delay:
  * pyqtgraph instead of matplotlib  -> GUI redraw of all 16 channels
    takes a few milliseconds instead of ~1 second.
  * Latency cap: if the plot ever falls behind, samples older than
    MAX_BACKLOG_S are dropped, so displayed data is never more than
    ~1 s behind the electrodes. No infinite lag build-up.
  * Graph still refreshes once per second (250 samples per refresh),
    16 channels, per-channel Y autoscale.

Install (use Python 3.8+, NOT 3.6):
    py -m pip install bleak numpy pyqtgraph PyQt5

Run from cmd:
    py ironbci16_fast.py
    py ironbci16_fast.py --address XX:XX:XX:XX:XX:XX
    py ironbci16_fast.py --csv eeg.csv --window 5
"""

import argparse
import asyncio
import sys
import threading
import time
from typing import Callable, Optional

import numpy as np

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("Missing bleak. Run:  py -m pip install bleak")

# ---------------------------------------------------------------- protocol --
DEVICE_NAME  = "IRONBCI_16"
NOTIFY_UUID  = "0000fe42-8e22-4541-9d4c-21edae82ed19"

N_CHANNELS      = 16
BYTES_PER_CH    = 3
PACKET_SIZE     = 96                 # 2 samples x 16 ch x 3 bytes
SAMPLE_RATE     = 250.0              # SPS  (CONFIG1 = 0x96)
VREF, GAIN      = 4.5, 1.0
UV_PER_LSB      = (VREF / GAIN) / (2 ** 23) * 1e6   # ~0.536 uV

REFRESH_MS      = 1000               # redraw once per second
MAX_BACKLOG_S   = 1.0                # drop data older than this -> bounded lag


def parse_packet(payload: bytes) -> np.ndarray:
    """96-byte notification -> (2, 16) float64 in uV. Sign-extended 24-bit BE."""
    usable = (len(payload) // (N_CHANNELS * BYTES_PER_CH)) * N_CHANNELS * BYTES_PER_CH
    if usable == 0:
        return np.empty((0, N_CHANNELS))
    raw = np.frombuffer(payload[:usable], dtype=np.uint8).reshape(-1, 3)
    v = (raw[:, 0].astype(np.int32) << 16) | (raw[:, 1].astype(np.int32) << 8) | raw[:, 2]
    v = np.where(v & 0x800000, v - 0x1000000, v)
    return v.reshape(-1, N_CHANNELS).astype(np.float64) * UV_PER_LSB


# --------------------------------------------------------------------- BLE --
class IronBCI16:
    """BLE client in a background thread. Fast, lock-light, bounded buffer."""

    def __init__(self, address: Optional[str] = None,
                 on_samples: Optional[Callable[[np.ndarray], None]] = None):
        self.address = address
        self.on_samples = on_samples
        self._chunks = []                     # list of (n,16) arrays
        self._lock = threading.Lock()
        self._thread = None
        self._loop = None
        self._stop_evt = None
        self.connected = threading.Event()
        self.error: Optional[str] = None
        self.n_samples_total = 0

    def start(self):
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def stop(self):
        if self._loop and self._stop_evt:
            self._loop.call_soon_threadsafe(self._stop_evt.set)
        if self._thread:
            self._thread.join(timeout=5)

    def get_new_data(self) -> np.ndarray:
        """All samples since last call, shape (n, 16). O(1) swap, no per-sample loop."""
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.empty((0, N_CHANNELS))
        return np.vstack(chunks)

    # internals ------------------------------------------------------------
    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except Exception as e:                                   # noqa: BLE001
            self.error = str(e)
        finally:
            self._loop.close()
            self.connected.clear()

    async def _run(self):
        self._stop_evt = asyncio.Event()
        addr = self.address
        if addr is None:
            print(f"Scanning for '{DEVICE_NAME}' ...")
            dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=15.0)
            if dev is None:
                raise RuntimeError(f"'{DEVICE_NAME}' not found. Board on? "
                                   f"Not connected to a phone/other app?")
            addr = dev.address
            print(f"Found {dev.name} @ {addr}")

        max_chunks = int(MAX_BACKLOG_S * SAMPLE_RATE / 2) + 8    # 2 samples/chunk

        def _handle(_c, data: bytearray):
            s = parse_packet(bytes(data))
            if s.size == 0:
                return
            self.n_samples_total += s.shape[0]
            with self._lock:
                self._chunks.append(s)
                # latency cap: consumer stalled -> drop oldest, keep real-time
                if len(self._chunks) > max_chunks:
                    del self._chunks[:len(self._chunks) - max_chunks]
            if self.on_samples:
                try:
                    self.on_samples(s)
                except Exception:                                # noqa: BLE001
                    pass

        async with BleakClient(addr) as client:
            print("Connected. Enabling notifications (starts the stream)...")
            await client.start_notify(NOTIFY_UUID, _handle)
            self.connected.set()
            await self._stop_evt.wait()
            try:
                await client.stop_notify(NOTIFY_UUID)
            except Exception:                                    # noqa: BLE001
                pass
        print("Disconnected.")


# ---------------------------------------------------------- pyqtgraph GUI --
def run_plot(dev: IronBCI16, window_seconds: float, csv_path: Optional[str]):
    try:
        import pyqtgraph as pg
        from pyqtgraph.Qt import QtCore, QtWidgets
    except ImportError:
        sys.exit("Missing pyqtgraph. Run:  py -m pip install pyqtgraph PyQt5")

    win_n = int(window_seconds * SAMPLE_RATE)
    data = np.zeros((win_n, N_CHANNELS))
    t_axis = np.arange(win_n) / SAMPLE_RATE
    csv_file = open(csv_path, "w") if csv_path else None
    if csv_file:
        csv_file.write(",".join(f"ch{i+1}_uV" for i in range(N_CHANNELS)) + "\n")

    pg.setConfigOptions(antialias=False, useOpenGL=False)  # fastest, most compatible
    app = QtWidgets.QApplication([])
    win = pg.GraphicsLayoutWidget(title="IRONBCI_16 - 250 SPS, 1 s refresh")
    win.resize(1200, 850)

    curves = []
    plots = []
    for ch in range(N_CHANNELS):
        row, col = ch % 8, ch // 8            # ch1-8 left, ch9-16 right
        p = win.addPlot(row=row, col=col)
        p.setLabel("left", f"ch{ch+1}")
        p.showGrid(x=True, y=True, alpha=0.2)
        p.enableAutoRange(axis="y", enable=True)   # per-channel Y autoscale
        p.setXRange(0, window_seconds, padding=0)
        p.setMouseEnabled(x=False, y=False)
        if row < 7:
            p.hideAxis("bottom")
        curves.append(p.plot(t_axis, data[:, ch], pen=pg.mkPen(width=1)))
        plots.append(p)
    win.show()

    state = {"pending": np.empty((0, N_CHANNELS)), "filled": 0}

    def update():
        new = dev.get_new_data()
        if new.size:
            if csv_file:
                np.savetxt(csv_file, new, delimiter=",", fmt="%.3f")
            state["pending"] = (np.vstack([state["pending"], new])
                                if state["pending"].size else new)
        pend = state["pending"]
        if pend.size == 0:
            return
        n = pend.shape[0]
        nonlocal data
        if n >= win_n:
            data = pend[-win_n:].copy()
        else:
            data = np.roll(data, -n, axis=0)
            data[-n:] = pend
        state["filled"] = min(state["filled"] + n, win_n)
        state["pending"] = np.empty((0, N_CHANNELS))

        for ch, c in enumerate(curves):
            c.setData(t_axis, data[:, ch])     # autoscale handled by pyqtgraph
        win.setWindowTitle(
            f"IRONBCI_16 - {dev.n_samples_total} samples "
            f"({dev.n_samples_total / SAMPLE_RATE:.0f} s)")
        if dev.error:
            print("BLE error:", dev.error)

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(REFRESH_MS)                    # 1 update per second

    try:
        app.exec_() if hasattr(app, "exec_") else app.exec()
    finally:
        if csv_file:
            csv_file.close()
        dev.stop()


# --------------------------------------------------------------------- CLI --
def main():
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8+ required (you have %d.%d). Install from python.org "
                 "and run with:  py script.py" % sys.version_info[:2])

    ap = argparse.ArgumentParser()
    ap.add_argument("--address", help="BLE MAC, skips scanning (faster startup)")
    ap.add_argument("--window", type=float, default=5.0, help="visible seconds")
    ap.add_argument("--csv", help="log all samples to CSV")
    args = ap.parse_args()

    dev = IronBCI16(address=args.address)
    dev.start()
    for _ in range(300):
        if dev.connected.is_set() or dev.error:
            break
        time.sleep(0.1)
    if dev.error:
        sys.exit("BLE error: " + dev.error)
    if not dev.connected.is_set():
        sys.exit("Timed out waiting for connection.")

    run_plot(dev, args.window, args.csv)


if __name__ == "__main__":
    main()
