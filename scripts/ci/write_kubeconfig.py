"""Normalize a CI secret and validate kubeconfig locally, without printing it."""
import base64
import binascii
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def candidates(value):
    """Try intact text first so valid JSON/YAML escapes are never rewritten."""
    pending = [value.lstrip("\ufeff")]
    seen = set()
    for _ in range(12):
        if not pending:
            return
        text = pending.pop(0)
        if text in seen:
            continue
        seen.add(text)
        yield text
        try:
            decoded = json.loads(text)
            if isinstance(decoded, str):
                pending.append(decoded)
        except (ValueError, TypeError):
            pass
        try:
            decoded = base64.b64decode("".join(text.split()), validate=True).decode("utf-8-sig")
            pending.append(decoded)
        except (ValueError, UnicodeError, binascii.Error):
            pass
        # Compatibility with secrets pasted as escaped YAML instead of JSON.
        if "\\n" in text:
            escaped = text.strip()
            if len(escaped) >= 2 and escaped[0] == escaped[-1] == '"':
                escaped = escaped[1:-1]
            pending.append(escaped.replace("\\r\\n", "\n").replace("\\n", "\n")
                           .replace("\\r", "\r").replace('\\"', '"'))


def write_kubeconfig(value, destination):
    if not value.strip():
        raise ValueError("KUBECONFIG_DATA is empty")
    destination = Path(destination)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".kubeconfig-", dir=destination.parent)
    temporary = Path(name)
    os.close(fd)
    try:
        for candidate in candidates(value):
            temporary.write_text(candidate.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
            temporary.chmod(0o600)
            # config view does not contact the cluster or execute auth plugins.
            result = subprocess.run(
                ["kubectl", "config", "view", "--kubeconfig", str(temporary), "--minify"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False,
            )
            if result.returncode == 0:
                os.replace(temporary, destination)
                return
        raise ValueError("Invalid kubeconfig: use complete YAML/JSON, Base64, or escaped YAML with a valid current-context")
    finally:
        temporary.unlink(missing_ok=True)


def main():
    os.umask(0o077)
    destination = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / ".kube/config")
    try:
        write_kubeconfig(os.environ.get("KUBECONFIG_DATA", ""), destination)
    except ValueError as error:
        # These messages are constants; never print input, kubectl stderr or credentials.
        print(f"kubeconfig setup failed: {error}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print("kubeconfig setup failed: cannot write config or run local kubectl validation", file=sys.stderr)
        return 1
    print("kubeconfig format validated locally")
    return 0


if __name__ == "__main__":
    sys.exit(main())
