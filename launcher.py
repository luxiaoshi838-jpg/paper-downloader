from __future__ import annotations

import app
from robust_resolver import RobustOpenAccessResolver

APP_VERSION = "1.1.0"


def main() -> None:
    app.APP_VERSION = APP_VERSION
    app.OpenAccessResolver = RobustOpenAccessResolver
    app.main()


if __name__ == "__main__":
    main()
