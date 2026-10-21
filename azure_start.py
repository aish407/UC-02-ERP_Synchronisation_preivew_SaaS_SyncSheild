"""Azure App Service entry point for the SyncShield HTTP application."""
from http.server import ThreadingHTTPServer

import server


if __name__ == "__main__":
    host = "0.0.0.0"
    http_server = ThreadingHTTPServer((host, server.PORT), server.Handler)
    print(f"SyncShield ready on {host}:{server.PORT}", flush=True)
    http_server.serve_forever()
