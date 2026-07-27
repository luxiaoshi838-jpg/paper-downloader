from __future__ import annotations

import app
from enhanced_app import EnhancedPaperDownloaderApp
from resolver_v13 import ResponsiveOpenAccessResolver

APP_VERSION = "1.2.1"


def main() -> None:
    app.APP_VERSION = APP_VERSION
    app.OpenAccessResolver = ResponsiveOpenAccessResolver
    application = EnhancedPaperDownloaderApp()
    application.mainloop()


if __name__ == "__main__":
    main()
