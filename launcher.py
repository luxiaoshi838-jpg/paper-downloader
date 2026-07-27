from __future__ import annotations

import app
from enhanced_app import EnhancedPaperDownloaderApp
from resolver_v15 import BrowserPublisherResolver

APP_VERSION = "1.4.1"


def main() -> None:
    app.APP_VERSION = APP_VERSION
    app.OpenAccessResolver = BrowserPublisherResolver
    application = EnhancedPaperDownloaderApp()
    application.mainloop()


if __name__ == "__main__":
    main()
