"""Local secret-file bootstrap; never overwrites credentials or prints secret values."""

import os, secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "secrets"
root.mkdir(exist_ok=True, mode=0o700)
os.chmod(root, 0o700)
for name, value in [
    ("demo_token", secrets.token_urlsafe(40)),
    ("anthropic_api_key", ""),
]:
    path = root / name
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        print("Retained existing", name)
    else:
        with os.fdopen(fd, "w") as stream:
            stream.write(value + "\n")
        print("Created", name, "(value not displayed)")
print(
    "Model execution remains disabled until you provision a real provider key and explicit model ID."
)
