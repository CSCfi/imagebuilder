#!/usr/bin/python3
"""
Reads and analyses the logs for imagebuilder
"""
import os
import sys

NAGIOS_STATE_OK             = 0
NAGIOS_STATE_WARNING        = 1
NAGIOS_STATE_CRITICAL       = 2




def get_start(lines: str, cloud:str) -> int:
    """
    Finds the indices of the first and last line of th
    """

    for line in reversed(lines):
        if f"===== {cloud} =====" in line:
            return lines.index(line)


    print("No runs in the log files")
    sys.exit(NAGIOS_STATE_OK)







def main() -> None:
    """
    Main entry point
    """

    cloud = os.getenv("IMAGEBUILDER_CHECK_CLOUD")
    filename = os.getenv("IMAGEBUILDER_CHECK_FILE")

    if cloud is None or filename is None:

        missing = [
            x for x in ["IMAGEBUILDER_CHECK_CLOUD", "IMAGEBUILDER_CHECK_FILE"]
            if os.getenv(x) is None
            ]

        raise EnvironmentError(f"Environtment variables are not set! ({', '.join(missing)})")


    try:
        with open(filename, "r", encoding="UTF-8") as f:
            lines = f.readlines()
    except IOError as e:
        print(f"Failed to open file: {e}")
        sys.exit(NAGIOS_STATE_CRITICAL)


    start = get_start(lines, cloud)

    lines = lines[start::]

    nagios_state = NAGIOS_STATE_OK
    nagios_output = ""


    for line in lines:
        # End marker
        if "===============" in line:
            break

        if nagios_state != NAGIOS_STATE_CRITICAL and "WARNING" in line:
            nagios_state = NAGIOS_STATE_WARNING

        nagios_state = NAGIOS_STATE_CRITICAL if "ERROR" in line else nagios_state


        if "INFO" in line and (line.split("INFO")[-1]).strip() == "":
            continue

        nagios_output += line


    print(nagios_output)
    sys.exit(nagios_state)




if __name__=="__main__":
    main()
