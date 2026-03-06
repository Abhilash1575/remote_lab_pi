#!/usr/bin/env python3
"""
Simple relay control script using lgpio
Usage: python3 relay_control.py on|off
"""

import lgpio
import sys
import os

RELAY_PIN = 16  # Your relay is connected to GPIO 16

def relay_on():
    """Turn the relay ON (power supply to experiments)"""
    try:
        # Open chip, claim pin, write - all in one go
        h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(h, RELAY_PIN)
        lgpio.gpio_write(h, RELAY_PIN, 0)  # ACTIVE LOW - relay ON when low
        lgpio.gpiochip_close(h)
        print("Relay ON")
        return True
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False

def relay_off():
    """Turn the relay OFF (power supply to experiments off)"""
    try:
        # Open chip, claim pin, write - all in one go
        h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(h, RELAY_PIN)
        lgpio.gpio_write(h, RELAY_PIN, 1)  # ACTIVE LOW - relay OFF when high
        lgpio.gpiochip_close(h)
        print("Relay OFF")
        return True
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 relay_control.py on|off", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "on":
        success = relay_on()
    elif cmd == "off":
        success = relay_off()
    else:
        print("Invalid command! Use on or off", file=sys.stderr)
        sys.exit(1)
    
    sys.exit(0 if success else 1)
