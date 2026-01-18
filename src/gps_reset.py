import serial
import time

ser = serial.Serial('/dev/ttyS0', 9600, timeout=1)
# Befehl für einen Hot Start / Reset des Moduls
ser.write(b"$PMTK101*32\r\n") 
time.sleep(1)
print("Reset-Befehl gesendet. Prüfe jetzt erneut den Antennenstatus.")
ser.close()