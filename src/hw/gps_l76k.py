from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import json
import socket

@dataclass
class GPSData:
    lat: Optional[float] = None
    lon: Optional[float] = None
    speed_kmh: float = 0.0
    sats: Optional[int] = 0
    fix: bool = False

class GPS_L76K:
    """
    GPS-Leser für den L76K-HAT über gpsd (JSON-Stream).
    Erwartet einen laufenden gpsd auf localhost:2947.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 2947, timeout: float = 0.5) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buffer = b""
        self._connected = False
        self._data = GPSData()

    def start(self) -> None:
        """Verbindung zu gpsd herstellen und WATCH aktivieren."""
        if self._connected:
            return

        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.settimeout(self.timeout)
            # WATCH-Command: JSON-Ausgabe von gpsd anfordern
            sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
            self._sock = sock
            self._connected = True
            print("🛰️ [GPS] Verbunden mit gpsd (JSON Mode).")
        except OSError as e:
            print(f"⚠️ [GPS] Verbindung zu gpsd fehlgeschlagen: {e}")
            self._sock = None
            self._connected = False

    def stop(self) -> None:
        """Verbindung zu gpsd schließen."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._connected = False
        print("[GPS] Verbindung zu gpsd geschlossen.")

    def _read_message(self) -> Optional[dict]:
        """Liest eine JSON-Zeile aus dem gpsd-Stream."""
        if not self._connected or self._sock is None:
            return None

        try:
            chunk = self._sock.recv(4096)
            if not chunk:
                self._connected = False
                return None

            self._buffer += chunk
            if b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    return None
                try:
                    return json.loads(line.decode("ascii", "ignore"))
                except json.JSONDecodeError:
                    return None
        except socket.timeout:
            return None
        except OSError:
            self._connected = False
            return None
        return None

    def get_data(self) -> GPSData:
        """Wird zyklisch von main.py aufgerufen."""
        if not self._connected:
            self.start()
            if not self._connected:
                return self._data

        # Wir lesen bis zu 10 Nachrichten, um den Puffer aktuell zu halten
        for _ in range(10):
            msg = self._read_message()
            if msg is None:
                break

            cls = msg.get("class")
            # TPV = Time Position Velocity (Koordinaten & Speed)
            if cls == "TPV":
                self._data.lat = msg.get("lat")
                self._data.lon = msg.get("lon")
                speed = msg.get("speed") or 0.0 # speed ist in m/s bei gpsd
                self._data.speed_kmh = float(speed) * 3.6
                mode = msg.get("mode") or 0
                self._data.fix = mode >= 2

            # SKY = Satellite Information
            elif cls == "SKY":
                sats = msg.get("satellites") or []
                used = [s for s in sats if s.get("used")]
                self._data.sats = len(used)

        return self._data