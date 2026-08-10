"""Canonical launcher that applies campaign config before importing MAT-BOT."""
from __future__ import annotations

import argparse
import sys

from tools.practice_eval import campaign_config


def build_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--campaign", required=True,
                        choices=sorted(campaign_config.CAMPAIGNS))
    parser.add_argument("--override", action="append", default=[])
    return parser


def main(argv=None):
    args, runner_args = build_parser().parse_known_args(argv)
    try:
        campaign_config.apply_campaign_environment(
            args.campaign, args.override)
    except campaign_config.CampaignConfigurationError as error:
        print(f"HARNESS CONFIGURATION ERROR: {error}", file=sys.stderr)
        return 2

    # Deliberately late: model/runtime constants read their final environment
    # only after the allowlisted campaign settings have been installed.
    from tools.practice_eval import runner
    return runner.main(runner_args)


if __name__ == "__main__":
    sys.exit(main())
