"""A notify program: appends its arguments, as JSON, to the file named first."""
import json
import sys

with open(sys.argv[1], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[2:]) + "\n")
