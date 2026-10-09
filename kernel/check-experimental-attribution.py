"""Fail closed on the experiment's resolved Kconfig, not its requested symbols."""

import argparse
import json
from pathlib import Path


def check_config(path, contract):
    values = {}
    for line in Path(path).read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key.removeprefix("CONFIG_")] = value
    errors = [
        f"CONFIG_{symbol} must resolve to y (got {values.get(symbol, 'unset')})"
        for symbol in contract["enables"]
        if values.get(symbol) != "y"
    ]
    errors += [
        f"CONFIG_{symbol} must be disabled (got {values[symbol]})"
        for symbol in contract["disables"]
        if values.get(symbol, "n") != "n"
    ]
    if values.get("LSM") != json.dumps(contract["lsm"]):
        errors.append(f'CONFIG_LSM must be "{contract["lsm"]}"')
    if errors:
        raise ValueError("\n".join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    try:
        check_config(args.config, json.loads(args.contract.read_text()))
    except ValueError as error:
        parser.exit(1, f"experimental-attribution config rejected:\n{error}\n")


if __name__ == "__main__":
    main()
