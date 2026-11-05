"""One-time Google login for Gmail + Calendar.

Run this on YOUR computer (it opens a browser):

    python scripts/google_auth.py path/to/client_secret.json

It writes data/google_token.json. Copy that file to the same place on the server.
See the README for how to get client_secret.json from Google Cloud Console.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from cuppy.tools.google_tools import SCOPES  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/google_auth.py path/to/client_secret.json")
    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0)
    out = Path("data/google_token.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json())
    print(f"Saved {out}. Keep it private - it gives access to your Gmail and Calendar.")


if __name__ == "__main__":
    main()
