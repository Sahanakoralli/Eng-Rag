"""Caption cache: never pay twice to describe the same image."""
import hashlib
import json
from pathlib import Path


class CaptionCache:
    def __init__(self, cache_path: Path, describe_fn, log=print):
        self.path = Path(cache_path)
        self.fn = describe_fn
        self.log = log
        self.data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        self.api_calls = 0

    def __call__(self, png: bytes) -> str:
        key = hashlib.sha256(png).hexdigest()
        if key in self.data:
            return self.data[key]
        try:
            caption = self.fn(png)
        except Exception as e:  # noqa: BLE001 - a failed caption must not fail the document
            self.log(f"    caption failed: {type(e).__name__}: {str(e)[:120]}")
            return ""  # not cached, so it is retried next run
        self.api_calls += 1
        self.data[key] = caption
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), "utf-8")
        return caption
