#!/usr/bin/python3
"""
Reads and analyses the logs for imagebuilder
"""
import time
import os
import sys
import json

NAGIOS_STATE_OK = 0
NAGIOS_STATE_WARNING = 1
NAGIOS_STATE_CRITICAL = 2

WAITED_FOR_TOO_LONG = 90000  # 25 hours

def pretty_list(human_time):
    """Prettify a list in English"""
    if len(human_time) > 1:
        return " ".join([", ".join(human_time[:-1]), "and", human_time[-1]])
    if len(human_time) == 1:
        return human_time[0]
    return ""

def format_duration(seconds, granularity = 2):
    """Format a number in seconds into readable human time"""
    intervals = (("hours", 3600), ("minutes", 60), ("seconds", 1))
    human_time = []
    for name, count in intervals:
        value = seconds // count
        if value:
            seconds -= value * count
            if value == 1:
                name = name.rstrip("s")
            human_time.append(f"{value} {name}")
    if not human_time:
        return "0 seconds"
    human_time = human_time[:granularity]
    return pretty_list(human_time)

def get_run_data(filename: str) -> dict:
    """
    Loads filename and finds the last run

    Returns
    -------
    dict
        Dictionary containing the data of the last run
    """
    json_data = { "errors": [] }
    try:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.readlines()
    except IOError as error:
        print(f"Failed to open file: {error}")
        sys.exit(NAGIOS_STATE_CRITICAL)
    if not content:
        print("The given log file is empty and therefore cannot be analyzed")
        print(
            "This is most likely caused by the log being rotated and it will fix itself"
        )
        sys.exit(NAGIOS_STATE_WARNING)
    for line in reversed(content):
        if '"error"' in line and json_data:
            json_data["errors"].append(json.loads(line))
        if '"summary"' in line:
            if json_data == { "errors": [] }:
                try:
                    json_data = json.loads(line)
                    json_data["errors"] = []
                except json.decoder.JSONDecodeError as error:
                    print(f"Log json '{filename}' could not be decoded: {error}")
                    sys.exit(NAGIOS_STATE_CRITICAL)
            else:
                break # Stop reading when the second 'summary' has been found
    if json_data != { "errors": [] }:
        return json_data
    print(f"No finished runs in the log file '{filename}'!")
    sys.exit(NAGIOS_STATE_CRITICAL)


def main() -> None:
    """
    Main entry point
    """

    cloud = os.getenv("IMAGEBUILDER_CHECK_CLOUD")
    filename = os.getenv("IMAGEBUILDER_CHECK_FILE")
    input_json = os.getenv("IMAGEBUILDER_INPUT_FILE")

    if cloud is None or filename is None:

        missing = [
            x
            for x in [
                "IMAGEBUILDER_CHECK_CLOUD",
                "IMAGEBUILDER_CHECK_FILE",
                "IMAGEBUILDER_INPUT_FILE",
            ]
            if os.getenv(x) is None
        ]

        raise EnvironmentError(
            f"Environment variables are not set! ({', '.join(missing)})"
        )

    input_json_data = None
    with open(input_json, "r", encoding="utf-8") as f:
        input_json_data = json.load(f)

    if input_json_data is None:
        print(f"Failed to read json file: {input_json}")
        sys.exit(NAGIOS_STATE_CRITICAL)

    run_data = get_run_data(filename)

    if time.time() - run_data["timestamp"] > WAITED_FOR_TOO_LONG:
        print(
            f"Imagebuilder was run last time more than {WAITED_FOR_TOO_LONG/3600} hours ago!"
        )
        sys.exit(NAGIOS_STATE_CRITICAL)

    nagios_state = NAGIOS_STATE_OK
    nagios_output = f"Last run {format_duration(time.time() - run_data['timestamp'])} ago.\n"
    for error in run_data["errors"]:
        nagios_output += f"Error in last run: {error}\n"

    for image_list in ("current", "deprecated"):
        nagios_output += f"{image_list} images:\n"
        images = [v["image_name"] for v in input_json_data[image_list]]
        seen_images = []

        for img in run_data["summary"][image_list]:
            nagios_output += f"  - {img}\n"
            seen_images.append(img)

        if set(images) - set(seen_images):  # Not seen
            nagios_output += (
                f"Images not seen in the log that should've been there ({image_list}): "
                f"{set(images) - set(seen_images)}\n"
            )
            nagios_state = NAGIOS_STATE_CRITICAL

        if set(seen_images) - set(images):  # Extra images
            nagios_output += (
                f"Images seen in the log that were not supposed to be there ({image_list}): "
                f"{set(seen_images) - set(images)}\n"
            )
            nagios_state = NAGIOS_STATE_CRITICAL
    if 'exit_code' in run_data["summary"]:
        if run_data["summary"]['exit_code'] == 1 and nagios_state != NAGIOS_STATE_CRITICAL:
            nagios_output += (
                "Last exit code for imagebuilder was "
                + f"{run_data['summary']['exit_code']}.\n"
            )
            nagios_state = NAGIOS_STATE_WARNING
        if run_data["summary"]['exit_code'] > 1:
            nagios_output += (
                "Last exit code for imagebuilder was "
                + f"{run_data['summary']['exit_code']}.\n"
            )
            nagios_state = NAGIOS_STATE_CRITICAL
    else:
        nagios_output += "Last exit code for imagebuilder was not summarized."
        nagios_state = NAGIOS_STATE_WARNING

    print(nagios_output.strip())
    sys.exit(nagios_state)


if __name__ == "__main__":
    main()
