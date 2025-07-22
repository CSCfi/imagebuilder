A simple program to analyse the output of fetch.py

No dependencies, just run it with
```
IMAGEBUILDER_CHECK_CLOUD="mycloud" IMAGEBUILDER_CHECK_FILE="mycloud.log" IMAGEBUILDER_INPUT_FILE="input.json" python3 check.py
```

Multiple clouds in a single log file are supported.

If a run has not happened in the interval of WAITED_FOR_TOO_LONG then an error will be thrown