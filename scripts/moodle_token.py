"""Get a Moodle web-service token (the same kind the official Moodle app uses).

    python scripts/moodle_token.py https://moodle.youruniversity.edu

It asks for your Moodle username and password, prints a token, and does NOT
store your password. Put the token in .env as MOODLE_TOKEN.

If your university uses single sign-on (Microsoft/Google login) this may not
work; instead look in Moodle > Preferences > Security keys for a
"Moodle mobile web service" key and copy that.
"""

import getpass
import sys

import httpx


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/moodle_token.py https://moodle.youruniversity.edu")
    base = sys.argv[1].rstrip("/")
    username = input("Moodle username: ")
    password = getpass.getpass("Moodle password (not stored): ")
    resp = httpx.post(
        f"{base}/login/token.php",
        data={"username": username, "password": password, "service": "moodle_mobile_app"},
        timeout=30,
    )
    data = resp.json()
    if "token" not in data:
        sys.exit(f"Moodle said: {data.get('error', data)}\n(Your university may block this - see the docstring.)")
    print(f"\nMOODLE_URL={base}\nMOODLE_TOKEN={data['token']}\n\nAdd these two lines to your .env file.")


if __name__ == "__main__":
    main()
