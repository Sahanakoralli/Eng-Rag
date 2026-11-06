"""Send OTP text messages through Fast2SMS (https://www.fast2sms.com). Standard library only.

Environment variables:
    FAST2SMS_API_KEY      your key from the Fast2SMS dashboard -> Dev API
    FAST2SMS_ROUTE        "otp" (default, Fast2SMS's ready-made OTP route) or "dlt" (your own DLT-approved template)
    FAST2SMS_SENDER_ID    only for route "dlt": your DLT header / sender id
    FAST2SMS_TEMPLATE_ID  only for route "dlt": the message id Fast2SMS shows for your approved template
    OTP_DEV_MODE=1        no SMS is sent; the code is printed in the server log (for local testing only)
"""
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("engrag.sms")
URL = "https://www.fast2sms.com/dev/bulkV2"


class SmsError(Exception):
    """The SMS could not be sent."""


def send_otp(phone: str, code: str) -> None:
    """`phone` is the 10-digit Indian mobile number (no +91)."""
    key = os.environ.get("FAST2SMS_API_KEY", "").strip()
    if not key:
        if os.environ.get("OTP_DEV_MODE") == "1":
            log.warning("OTP_DEV_MODE: the OTP for %s is %s (no SMS sent)", phone, code)
            return
        raise SmsError("FAST2SMS_API_KEY is not set")

    route = os.environ.get("FAST2SMS_ROUTE", "otp").strip().lower()
    payload = {"route": "q", "numbers": phone, "message": code}
    if route == "dlt":
        try:
            payload.update(sender_id=os.environ["FAST2SMS_API_KEY"],
                           flash="0")
        except KeyError as e:
            raise SmsError(f"{e.args[0]} is required for the dlt route") from e

    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"authorization": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:  # Fast2SMS answers errors (bad key, no balance...) with a JSON body
        try:
            body = json.loads(e.read().decode() or "{}")
        except ValueError:
            body = {}
        body.setdefault("return", False)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SmsError(f"could not reach Fast2SMS: {e}") from e

    if not body.get("return"):
        log.error("Fast2SMS rejected the request: %s", body)
        raise SmsError(f"Fast2SMS error: {body.get('message')}")



