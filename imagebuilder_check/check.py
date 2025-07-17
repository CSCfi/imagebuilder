#!/usr/bin/python3
"""
Reads and analyses the logs for imagebuilder
"""
from datetime import datetime
import os
import sys
import json

NAGIOS_STATE_OK             = 0
NAGIOS_STATE_WARNING        = 1
NAGIOS_STATE_CRITICAL       = 2

WAITED_FOR_TOO_LONG = 90000 # 25 hours


def get_lines(filename: str, cloud: str) -> list:
    """
    Loads filename and finds the last run of cloud
    
    Returns
    -------
    list[str]
        List of lines from filename starting at the last run of cloud
    """

    try:
        with open(filename, "r", encoding="UTF-8") as f:
            lines = f.readlines()
    except IOError as e:
        print(f"Failed to open file: {e}")
        sys.exit(NAGIOS_STATE_CRITICAL)




    for line in reversed(lines):
        if f"===== {cloud} =====" in line:
            start = lines.index(line)
            return lines[start::]


    print("No runs in the log files!")
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
            x for x in
                ["IMAGEBUILDER_CHECK_CLOUD", "IMAGEBUILDER_CHECK_FILE", "IMAGEBUILDER_INPUT_FILE"]
            if os.getenv(x) is None
            ]

        raise EnvironmentError(f"Environtment variables are not set! ({', '.join(missing)})")


    json_data = None
    with open(input_json, "r", encoding="utf-8") as f:
        json_data = json.load(f)

    if json_data is None:
        print(f"Failed to read json file: {input_json}")
        sys.exit(NAGIOS_STATE_CRITICAL)

    images = [v["image_name"] for s in ("current", "deprecated") for v in json_data[s]]
    seen_images = []


    lines = get_lines(filename, cloud)

    last_ran = datetime.strptime(lines[0].split(",")[0], "%Y-%m-%d %H:%M:%S")
    if (datetime.now() - last_ran).seconds > WAITED_FOR_TOO_LONG:
        print("Imagebuilder has not been run in a while!")
        sys.exit(NAGIOS_STATE_CRITICAL)



    nagios_state = NAGIOS_STATE_OK
    nagios_output = ""


    for line in lines:
        # End marker
        if "===============" in line:
            break

        if nagios_state != NAGIOS_STATE_CRITICAL and "WARNING" in line:
            nagios_state = NAGIOS_STATE_WARNING

        nagios_state = NAGIOS_STATE_CRITICAL if "ERROR" in line else nagios_state


        if "INFO" in line and not (
            "===" in line or
            "IMGBUILDER_OUTPUT" in line or
            "removed completely" in line or
            "is still used by someone" in line
            ):
            continue

        if "INFO" in line and "===" in line and "====" not in line:
            seen_images.append(
                (line.split("===")[1]).strip()
            )

        nagios_output += line


    print(nagios_output)

    not_seen_images = set(images) - set(seen_images)
    extra_images = set(seen_images) - set(images)

    if not_seen_images:
        print(f"Images not seen in the output that should've been there: {not_seen_images}")
        nagios_state = NAGIOS_STATE_CRITICAL

    if extra_images:
        print(f"Images seen that were not supposed to be there: {extra_images}")
        nagios_state = NAGIOS_STATE_CRITICAL


    sys.exit(nagios_state)




if __name__=="__main__":
    main()
