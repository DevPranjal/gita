import os
import json
import sys
import logging

from pathlib import Path


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    data = json.loads(Path(sys.argv[1]).read_text())
    print(os.path.abspath(data["target"]))


if __name__ == "__main__":
    main()
