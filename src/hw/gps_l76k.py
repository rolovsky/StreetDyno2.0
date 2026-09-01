from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import json
import socket
import time
from datetime import datetime

@dataclass
class GPSData:
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt: Optional[float] = 0.0
    speed_kmh: float = 0.0
    sats: Optional[int] = 0
    fix: bool = False
    timestamp: Optional[datetime] = None

class GPS_L76K:
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
        if self._connected:
            return
        try:
            sock = socket.create_connection((self.host, self.port), timeout=2.0)
            sock.settimeout(self.timeout)
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
        if not self._connected or self._sock is None:
            self.start()
            if not self._connected:
                time.sleep(2)
                return
        try:
            chunk = self._sock.recv(4096)
            if not chunk:
                self._connected = False
                return
            self._buffer += chunk
            
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
                        self._data.alt = float(msg.get("alt") or msg.get("altHAE") or msg.get("altMSL") or 0.0)
                        
                        # --- HIER IST DER FIX: Exakt * 3.6 ---
                        speed = msg.get("speed") or 0.0 
                        self._data.speed_kmh = float(speed) * 3.6
                        
                        mode = msg.get("mode") or 0
                        self._data.fix = mode >= 2
                        
                        time_str = msg.get("time")
                        if time_str:
                            try:
                                parsed_time = datetime.strptime(time_str[:19], "%Y-%m-%dT%H:%M:%S")
                                self._data.timestamp = parsed_time
                            except ValueError:
                                pass

                    elif cls == "SKY":
                        sats = msg.get("satellites") or []
                        used = [s for s in sats if s.get("used")]
                        self._data.sats = len(used)
                except json.JSONDecodeError:
                    continue
        except socket.timeout:
            pass
        except OSError:
            self._connected = False

    def get_data(self) -> GPSData:
        if self._running:
            self._update_internal()
        return self._data