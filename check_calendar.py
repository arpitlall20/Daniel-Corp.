"""Test your iCloud calendar login on its own:   python check_calendar.py"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

email = os.getenv("ICLOUD_EMAIL", "").strip()
pw = os.getenv("ICLOUD_APP_PASSWORD", "").strip()

print(f"Email in .env:    {email or '(missing)'}")
print(f"Password in .env: {'(missing)' if not pw else f'{len(pw)} characters, starts with {pw[:2]}...'}")

problems = []
if not email:
    problems.append("ICLOUD_EMAIL is empty.")
if not pw:
    problems.append("ICLOUD_APP_PASSWORD is empty.")
elif len(pw.replace("-", "")) != 16:
    problems.append("An app-specific password is 16 letters (xxxx-xxxx-xxxx-xxxx). "
                    "This looks like your normal Apple ID password, which iCloud will reject.")
if pw.startswith(("'", '"')) or " " in pw:
    problems.append("Remove quotes and spaces around the password in .env.")
for p in problems:
    print("  ! " + p)

try:
    import caldav
    client = caldav.DAVClient(url="https://caldav.icloud.com", username=email, password=pw)
    cals = client.principal().calendars()
    print("\nSUCCESS - connected to iCloud. Your calendars:")
    for c in cals:
        print("  -", c.name)
    print("\nPut the one you want in .env as ICLOUD_CALENDAR_NAME, then restart web.py.")
except Exception as e:
    print(f"\nFAILED: {type(e).__name__}: {str(e)[:200]}")
    if "Unauthorized" in str(e) or "401" in str(e):
        print("""
iCloud rejected the login. Check:
  1. ICLOUD_EMAIL is your Apple ID email exactly (Settings > [your name] on iPhone shows it).
     It may be a Gmail/Outlook address, not @icloud.com.
  2. ICLOUD_APP_PASSWORD is an app-specific password from appleid.apple.com >
     Sign-In and Security > App-Specific Passwords (create a new one if unsure).
  3. You saved .env, then restarted web.py.""")
