import RPi.GPIO as GPIO
import time

PIN = 24  # Dein RPM_PIN aus der config.py

GPIO.setmode(GPIO.BCM)
# Wie in deiner neuen rpm_input.py: Pull-Up nutzen
GPIO.setup(PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

print("Teste Optokoppler... Druecke Strg+C zum Beenden.")

def callback_funktion(channel):
    print(f"Impuls erkannt am Pin {channel}!")

# Auf fallende Flanke reagieren
GPIO.add_event_detect(PIN, GPIO.FALLING, callback=callback_funktion)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    GPIO.cleanup()