#!/usr/bin/python3
"""
Reads and analyses the logs for imagebuilder
"""
from datetime import datetime
import os
import sys
import json

NAGIOS_STATE_OK = 0
NAGIOS_STATE_WARNING = 1
NAGIOS_STATE_CRITICAL = 2

WAITED_FOR_TOO_LONG = 90000  # 25 hours


def get_run_data(filename: str, cloud: str) -> dict:
    """
    Loads filename and finds the last run of cloud

    Returns
    -------
    dict
        Dictionary containing the data of the last run
    """
    json_data = None
    try:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.readlines)
            print(f"Log contain {len(content)} lines")
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
        if '"summary"' in line:
            try:
                json_data = json.loads(line)
            except json.decoder.JSONDecodeError as error:
                print(f"Log json '{filename}' could not be decoded: {error}")
                sys.exit(NAGIOS_STATE_CRITICAL)
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

    run_data = get_run_data(filename, cloud)

    if datetime.now().total_seconds() - run_data["duration"] > WAITED_FOR_TOO_LONG:
        print(
            f"Imagebuilder was run last time more than {WAITED_FOR_TOO_LONG/3600} hours ago!"
        )
        sys.exit(NAGIOS_STATE_CRITICAL)

    nagios_state = NAGIOS_STATE_OK
    nagios_output = ""

    for image_list in ("current", "deprecated"):

        images = [v["image_name"] for v in input_json_data[image_list]]
        seen_images = []

        for img in run_data[image_list]:
            nagios_output += f"=== {img} ===\n"
            seen_images.append(img)

            for msg in run_data[image_list][img]["events"]:

                if msg["level"] == "WARNING":
                    if nagios_state != NAGIOS_STATE_CRITICAL:
                        nagios_state = NAGIOS_STATE_WARNING
                    nagios_output += msg["content"] + "\n"

                elif msg["level"] == "ERROR":
                    nagios_state = NAGIOS_STATE_CRITICAL
                    nagios_output += msg["content"] + "\n"

                elif msg.get("important"):
                    nagios_output += msg["content"] + "\n"

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

    print(nagios_output.strip())
    sys.exit(nagios_state)


if __name__ == "__main__":
    main()
