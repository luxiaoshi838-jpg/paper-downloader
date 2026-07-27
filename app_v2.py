import app as legacy
from resolver_v2 import OpenAccessResolverV2

legacy.APP_VERSION = "1.1.0"
legacy.OpenAccessResolver = OpenAccessResolverV2

if __name__ == "__main__":
    legacy.main()
