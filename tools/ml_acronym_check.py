"""Run frozen acronym cases through the existing offline quality checker.

Use its --cases and --expected-sha256 arguments. Only aliases are graded;
inference and public application behavior remain unchanged. Development cases
cannot establish release accuracy. Other finding categories are not certified.
"""

import sys

import ml_quality_check as quality


def main(argv=None):
    previous = quality.CATEGORIES
    quality.CATEGORIES = ("aliases",)
    try:
        return quality.main(argv)
    finally:
        quality.CATEGORIES = previous


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ML acronym check: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
