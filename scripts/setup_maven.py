"""Download the pinned official Maven binary into ignored project-local cache."""
import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

VERSION = "3.9.11"
ROOT = Path(__file__).resolve().parents[1] / ".cache/maven"
URL = f"https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/{VERSION}/apache-maven-{VERSION}-bin.zip"


def main():
    destination = ROOT / f"apache-maven-{VERSION}"
    if (destination / "bin/m2.conf").is_file():
        print(f"Maven already available: {destination}")
        return
    data = urllib.request.urlopen(URL, timeout=60).read()
    checksum = urllib.request.urlopen(URL + ".sha512", timeout=30).read().decode().split()[0]
    if hashlib.sha512(data).hexdigest() != checksum:
        raise RuntimeError("Maven SHA-512 checksum mismatch")
    ROOT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            if not (ROOT / name).resolve().is_relative_to(ROOT.resolve()):
                raise RuntimeError("Unsafe archive path")
        archive.extractall(ROOT)
    print(f"Maven {VERSION} downloaded and SHA-512 verified: {destination}")


if __name__ == "__main__":
    main()
