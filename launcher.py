from __future__ import annotations

import app
from enhanced_app import EnhancedPaperDownloaderApp
from resolver_v12 import EnhancedOpenAccessResolver

APP_VERSION = "1.2.0"


def main() -> None:
    app.APP_VERSION = APP_VERSION
    app.OpenAccessResolver = EnhancedOpenAccessResolver
    application = EnhancedPaperDownloaderApp()
    application.mainloop()


if __name__ == "__main__":
    main()
