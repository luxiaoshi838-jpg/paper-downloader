from __future__ import annotations

import app
from enhanced_app import EnhancedPaperDownloaderApp
from resolver_v14 import CampusNetworkResolver

APP_VERSION = "1.3.0"


def main() -> None:
    app.APP_VERSION = APP_VERSION
    app.OpenAccessResolver = CampusNetworkResolver
    application = EnhancedPaperDownloaderApp()
    application.mainloop()


if __name__ == "__main__":
    main()
