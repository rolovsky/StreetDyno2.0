from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import json
import socket
import time

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
    Optimiert für den Betrieb in einem Hintergrund-Thread.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 2947, timeout: float = 0.1) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buffer = b""
        self._connected = False
        self._data = GPSData()
        self._running = False

    def start(self) -> None:
        """Verbindung zu gpsd herstellen."""
        if self._connected:
            return

        try:
            # Kurzer Timeout für den Verbindungsaufbau
            sock = socket.create_connection((self.host, self.port), timeout=2.0)
            sock.settimeout(self.timeout)
            # WATCH-Command an gpsd senden
            sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
            self._sock = sock
            self._connected = True
            self._running = True
            print("🛰️ [GPS] Verbunden mit gpsd (JSON Mode).")
        except OSError as e:
            print(f"[GPS] Verbindung zu gpsd fehlgeschlagen: {e}")
            self._sock = None
            self._connected = False

    def stop(self) -> None:
        """Verbindung schließen und Thread-Schleife signalisieren zu stoppen."""
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._connected = False
        print("[GPS] Verbindung zu gpsd geschlossen.")

    def _update_internal(self) -> None:
        """
        Liest alle verfügbaren Daten vom Socket und aktualisiert den internen Status.
        Diese Methode wird im Thread kontinuierlich aufgerufen.
        """
        if not self._connected or self._sock is None:
            self.start()
            if not self._connected:
                time.sleep(2) # Wartezeit bei Verbindungsverlust
                return

        try:
            # Lies einen Block vom Socket
            chunk = self._sock.recv(4096)
            if not chunk:
                self._connected = False
                return

            self._buffer += chunk
            
            # Verarbeite alle vollständigen Zeilen im Puffer
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                
                try:
                    msg = json.loads(line.decode("ascii", "ignore"))
                    cls = msg.get("class")
                    
                    if cls == "TPV":
                        self._data.lat = msg.get("lat")
                        self._data.lon = msg.get("lon")
                        speed = msg.get("speed") or 0.0 # m/s
                        self._data.speed_kmh = float(speed) * 3.6
                        mode = msg.get("mode") or 0
                        self._data.fix = mode >= 2

                    elif cls == "SKY":
                        sats = msg.get("satellites") or []
                        used = [s for s in sats if s.get("used")]
                        self._data.sats = len(used)
                except json.JSONDecodeError:
                    continue

        except socket.timeout:
            # Ein Timeout ist hier okay, wir loopen einfach weiter
            pass
        except OSError:
            self._connected = False

    def get_data(self) -> GPSData:
        """
        Gibt die aktuellsten Daten zurück. 
        Wenn Threading aktiv ist, wird diese Methode in main.py aufgerufen.
        """
        # Falls wir nicht im Thread laufen, machen wir hier ein schnelles Update
        # Aber im Threading-Modus macht der Thread die Arbeit.
        if self._running:
            self._update_internal()
            
        return self._data