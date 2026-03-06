#!/usr/bin/env python3
"""
Simple relay control script using lgpio
Usage: python3 relay_control.py on|off
Tries multiple GPIO pins if first one is busy
"""

import lgpio
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Try these pins in order
RELAY_PINS = [26, 21, 20, 16, 12, 7]

# Keep a persistent handle to GPIO chip
gpio_handle = None
current_pin = None

def init_gpio():
    """Initialize GPIO chip handle"""
    global gpio_handle, current_pin
    
    # If already initialized, just check if still valid
    if gpio_handle is not None:
        return True
    
    for pin in RELAY_PINS:
        try:
            gpio_handle = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(gpio_handle, pin)
            current_pin = pin
            print(f"Using GPIO pin {pin}", file=sys.stderr)
            return True
        except Exception as e:
            print(f"GPIO {pin} failed: {e}", file=sys.stderr)
            gpio_handle = None
            try:
                lgpio.gpiochip_close(gpio_handle)
            except:
                pass
    
    return False

def relay_on():
    """Turn the relay ON (power supply to experiments)"""
    if not init_gpio():
        return False
    try:
        lgpio.gpio_write(gpio_handle, current_pin, 0)  # Most relay modules are ACTIVE LOW
        print("Relay ON")
        return True
    except Exception as e:
        print(f"Error turning relay ON: {e}", file=sys.stderr)
        return False

def relay_off():
    """Turn the relay OFF (power supply to experiments off)"""
    if not init_gpio():
        return False
    try:
        lgpio.gpio_write(gpio_handle, current_pin, 1)  # Most relay modules are ACTIVE LOW
        print("Relay OFF")
        return True
    except Exception as e:
        print(f"Error turning relay OFF: {e}", file=sys.stderr)
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
