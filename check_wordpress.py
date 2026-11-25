"""Test your WordPress connection on its own:   python check_wordpress.py"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

from team.tools.wordpress import wp_list, wp_site_info

url, user, pw = (os.getenv(k, "").strip() for k in ("WP_URL", "WP_USERNAME", "WP_APP_PASSWORD"))
print(f"WP_URL:          {url or '(missing)'}")
print(f"WP_USERNAME:     {user or '(missing)'}")
pw_len = len(pw.replace(" ", ""))
print(f"WP_APP_PASSWORD: {pw_len} characters" if pw else "WP_APP_PASSWORD: (missing)")
if not (url and user and pw):
    sys.exit("\nFill in WP_URL, WP_USERNAME and WP_APP_PASSWORD in .env first.")
if not url.startswith("https://"):
    print("  ! WP_URL should start with https:// (application passwords need HTTPS).")
if pw and pw_len != 24:
    print("  ! An application password is 24 characters (6 groups of 4). Is this your normal login password?")

try:
    print("\n" + wp_site_info())
    print("\nLatest posts:\n" + wp_list("post", count=5))
    print("\nSUCCESS - the Web & Content agent can reach your site.")
except Exception as e:
    print(f"\nFAILED: {e}")
    msg = str(e)
    if "401" in msg:
        print("Login rejected: check WP_USERNAME (your login name, not email) and create a fresh Application Password.")
    elif "404" in msg or "rest_no_route" in msg:
        print("The REST API wasn't found at this address. Check WP_URL, or a security plugin may be blocking /wp-json.")
    elif "urlopen error" in msg or "Name or service" in msg or "refused" in msg:
        print("Couldn't reach the site. Check WP_URL is your site's address, e.g. https://yoursite.com")
    elif "403" in msg:
        print("Access forbidden: your user needs the Editor or Administrator role, or a security plugin is blocking it.")
