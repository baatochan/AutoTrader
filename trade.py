#!/usr/bin/env python3
"""
AutoTrader, a script for automating trading in Pokémon GO on Android.
Author: jonaro00
"""

import asyncio
from dataclasses import dataclass
import random
import subprocess
import sys
import time
from pathlib import Path
from wakepy import keep

try:
    from ppadb.client_async import ClientAsync
    from ppadb.device_async import DeviceAsync
    import yaml
    from yaml.parser import ParserError
except ModuleNotFoundError as import_exc:
    print(import_exc)
    print('Run "pip install -r requirements.txt" to install required packages.')
    sys.exit(1)

CONFIG_FILE_DIR = '/storage/self/primary/'
CONFIG_FILE_NAME = 'AutoTraderConfig.yaml'
TMP_FILE_PATH = Path('tmp.yaml')
CONFIG = dict[str, list[int]]


@dataclass
class Button:
    """UI button metadata used in the trade automation sequence."""

    name: str
    delay_after: float
    use_delay_modifier: bool


BUTTONS = [
    Button(
        'TRADE_BTN',
        10,
        True,
    ),
    Button(
        'FIRST_PKMN_BTN',
        2,
        False,
    ),
    Button(
        'NEXT_BTN',
        8,
        True,
    ),
    Button(
        'CONFIRM_BTN',
        18,
        True,
    ),
    Button(
        'X_BTN',
        2,
        False,
    ),
]
BUTTON_NAMES = set(btn.name for btn in BUTTONS)
SLEEP_MODIFIER = 0


# pylint: disable=too-few-public-methods
class DeviceAsyncWrapper(DeviceAsync):
    """Async ADB device with loaded button coordinate config."""

    config: CONFIG


class AutoTraderError(Exception):
    """Custom exception for expected AutoTrader workflow failures."""


async def tap(device: DeviceAsyncWrapper, point: list[int]):
    """Sends a tap at point to device."""
    x, y = point
    x = max(round(random.uniform(x - 10, x + 10), 1), 0)
    y = max(round(random.uniform(y - 10, y + 10), 1), 0)
    # Uses a tiny swipe over 100±20 ms for increased reliability
    # and visibility on the Pointer Location tool.
    delay = int(round(random.uniform(80, 120), 0))
    # delay for "input swipe" action needs to be int
    await device.shell(f'input swipe {x} {y} {x+1} {y+1} {delay}')


async def trade_sequence(devices: list[DeviceAsyncWrapper]):
    """Sends taps to devices in a sequence with delays
    to complete a trade process. Device must have
    button coordinates stored in attribute `config`."""
    for btn in BUTTONS:
        delay = max(btn.delay_after +
                    (SLEEP_MODIFIER if btn.use_delay_modifier else 0), 0)
        # Adds a little bit of randomness to the time between clicks to prevent Niantic
        # from detecting the script
        delay = random.uniform(0.9 * delay, 1.1 * delay)
        print('    Sending', btn.name)

        # CONFIRM_BTN needs to be tapped sequentially on each device with a delay in between
        # to prevent "Cannot confirm yet" error. The other buttons can be tapped simultaneously
        # on all devices.
        if btn.name == 'CONFIRM_BTN':
            for i, dev in enumerate(devices):
                await tap(dev, dev.config[btn.name])
                if i < len(devices) - 1:
                    await asyncio.sleep(1)
        else:
            commands = (tap(dev, dev.config[btn.name]) for dev in devices)
            await asyncio.gather(*commands)

        await asyncio.sleep(delay)


async def trade_process(devices: list[DeviceAsyncWrapper], n_trades: int):
    """Executes `n_trades` trading sequences."""
    if n_trades < 1:
        return
    await pointer(devices, True)
    try:
        for i in range(1, n_trades+1):
            print(f'  Starting trade {i} of {n_trades}')
            await trade_sequence(devices)
    finally:
        await pointer(devices, False)


async def get_config(device: DeviceAsyncWrapper) -> CONFIG:
    """Pulls config file from device and parses it. Sets the `config` attribute on success."""
    config_file_path = CONFIG_FILE_DIR + CONFIG_FILE_NAME
    await device.pull(config_file_path, TMP_FILE_PATH)
    content = TMP_FILE_PATH.read_text(encoding='utf-8')
    TMP_FILE_PATH.unlink()
    if not content:
        raise AutoTraderError(f'Found no config file at {config_file_path}')
    config: CONFIG = yaml.safe_load(content)
    assert isinstance(
        config, dict), 'Incorrect config file format (should be an object with keys)'
    if not BUTTON_NAMES <= set(config.keys()):
        raise AutoTraderError(
            f'Missing config key(s): {BUTTON_NAMES - set(config.keys())}')
    for coords in config.values():
        assert isinstance(coords, list) and all(map(lambda i: isinstance(i, int), coords)), \
            'Invalid coords format in config (should be list with two integers)'
    device.config = config
    return config


async def set_setting(device: DeviceAsyncWrapper, namespace_and_key: str, value):
    """Wraps 'settings put' in adb shell. Sets key in namespace to value."""
    await device.shell(f'settings put {namespace_and_key} {value}')


async def pointer(devices: list[DeviceAsyncWrapper], on: bool):
    """Turns on/off pointer location setting on all `devices`."""
    for device in devices:
        try:
            await set_setting(device, 'system pointer_location', int(on))
        # Best-effort setting; do not interrupt trading flow if this toggle fails.
        # pylint: disable=broad-exception-caught
        except Exception as exc:
            print(
                f'Failed to turn {"on" if on else "off"} pointer location on', device.serial, exc)


async def start_server_if_needed():
    """Starts adb server if not already running."""
    try:
        # Checks if server is running by listing devices.
        await ClientAsync().devices()
    except RuntimeError:
        try:
            subprocess.run(['adb', 'start-server'], check=True)
        except Exception as exc:
            raise AutoTraderError(
                'Failed to start ADB server. Make sure adb is installed and in your PATH. '
                'You may also start the server manually if you don\'t want to put adb in your '
                'PATH.') from exc


async def setup() -> list[DeviceAsyncWrapper]:
    """Checks for devices and loads config files from devices."""
    await start_server_if_needed()
    client = ClientAsync()
    devices: list[DeviceAsyncWrapper] = await client.devices()
    if not devices:
        raise AutoTraderError('No devices found')
    print('Found devices:')
    for device in devices:
        print(' ', device.serial)
    print()
    current_serial = 'unknown device'
    try:
        for device in devices:
            current_serial = device.serial
            await get_config(device)
            print('Successfully loaded config from', device.serial)
    except (AutoTraderError, AssertionError, ParserError) as exc:
        raise AutoTraderError(
            f'Failed to load config from {current_serial}', *exc.args) from exc
    return devices


def print_help():
    """Prints the usage instructions."""
    print(
        "\n"
        "Enter the number of trades to run, or \n"
        "* 'h' or 'help' to show this message, \n"
        "* 'r' or 'reload' to detect new devices and reload configs, \n"
        "* 'delay <value>' to set an extra delay between clicks (can also be negative), or \n"
        "* 'q', 'quit', or 'exit' to quit (Ctrl+C is disabled).\n"
    )


def interface():
    """Runs the main loop asking for user input."""
    # Uses module-level state for interactive delay tuning command.
    global SLEEP_MODIFIER  # pylint: disable=global-statement
    print(
        '\n'
        ' ##                          ## \n'
        '##   AutoTrader by jonaro00   ##\n'
        '##    Tweaked by baatochan    ##\n'
        ' ##                          ## \n'
    )
    devices: list[DeviceAsyncWrapper] = asyncio.run(setup())
    print_help()
    while True:
        try:
            i = input("Number of trades? > ").strip()
            il = i.lower()
            if il in ('q', 'quit', 'exit'):
                break
            if il in ('h', 'help'):
                print_help()
                continue
            if il in ('r', 'reload'):
                devices = asyncio.run(setup())
                continue
            if il.startswith('delay'):
                args = i.split()
                args_l = len(args)
                if args_l == 2:
                    SLEEP_MODIFIER = float(args[1])
                print('Current extra delay:', SLEEP_MODIFIER)
                continue
            assert (n := int(i)) > 0
        except KeyboardInterrupt:
            print('\nPress Ctrl+C again to force quit.')
            try:
                time.sleep(0.5)
            except KeyboardInterrupt:
                break
            continue
        except EOFError:
            break
        except (ValueError, AssertionError):
            print("Invalid input. Enter a positive integer or 'h' for help.")
            continue
        try:
            print(f'Starting {n} trades (Ctrl+C to cancel)...')
            with keep.running():
                asyncio.run(trade_process(devices, n))
        except KeyboardInterrupt:
            continue
        # Intentionally broad to keep the input loop alive after unexpected runtime errors.
        # pylint: disable=broad-exception-caught
        except Exception as exc:
            print(exc)


def main():
    """Entry point that runs the interactive AutoTrader interface."""

    try:
        interface()
    except AutoTraderError as exc:
        print('\n'.join(map(str, exc.args)))
    # Intentionally broad as a final safety net to avoid ungraceful crashes.
    # pylint: disable=broad-exception-caught
    except Exception as exc:
        print('Unexpected error:', exc.__class__.__name__, exc.args)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
