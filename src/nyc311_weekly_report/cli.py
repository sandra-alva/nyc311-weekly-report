import argparse

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nyc311",
        description="NYC 311 Weekly Report pipeline (WIP)."
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit."
    )
    args = parser.parse_args()

    if args.version:
        print("nyc311-weekly-report v0.0.1")
        return

    parser.print_help()