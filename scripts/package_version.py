# package_version.py — 复现包版本标识(内容哈希清单;仓库工作树为脏,故
# git HEAD 不足以标识包内容,SHA-256 清单为权威标识)。
# 用法: python package_version.py <pkg_dir> [--derived-from <id>]
import hashlib
import os
import subprocess
import sys

PAYLOAD_DIRS = ("data", "code", "preregistration", "scripts")
EXCLUDE_NAMES = {"VERSION.txt", "MANIFEST.sha256",
                 "LICENSE_OPTIONS.md", "README.md", "CITATION.cff"}
EXCLUDE_EXT = {".lock", ".pyc"}


def payload_digest(pkg):
    lines = []
    # root-level payload files that belong to the immutable artifact
    for rootf in ("LICENSE",):
        rp = os.path.join(pkg, rootf)
        if os.path.isfile(rp):
            h = hashlib.sha256(open(rp, "rb").read()).hexdigest()
            lines.append("%s  %s" % (h, rootf))
    for d in PAYLOAD_DIRS:
        base = os.path.join(pkg, d)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for fn in sorted(files):
                if fn in EXCLUDE_NAMES or os.path.splitext(fn)[1] in EXCLUDE_EXT:
                    continue
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, pkg).replace("\\", "/")
                h = hashlib.sha256()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                lines.append("%s  %s" % (h.hexdigest(), rel))
    lines.sort(key=lambda l: l.split("  ", 1)[1])
    blob = "\n".join(lines) + "\n"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest(), lines


def main():
    pkg = sys.argv[1]
    derived = None
    if "--derived-from" in sys.argv:
        derived = sys.argv[sys.argv.index("--derived-from") + 1]
    try:
        head = subprocess.run(["git", "-C", os.getcwd(), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        dirty = len([l for l in subprocess.run(
            ["git", "-C", os.getcwd(), "status", "--short"],
            capture_output=True, text=True).stdout.splitlines() if l.strip()])
    except Exception:
        head, dirty = "unknown", -1
    digest, lines = payload_digest(pkg)
    manifest = os.path.join(pkg, "MANIFEST.sha256")
    with open(manifest, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")
    vid = "sha256:%s" % digest[:16]
    with open(os.path.join(pkg, "VERSION.txt"), "w",
              encoding="utf-8", newline="") as fh:
        fh.write("package-version: %s\n" % vid)
        fh.write("payload-sha256: %s\n" % digest)
        fh.write("files-in-manifest: %d\n" % len(lines))
        fh.write("git-commit-at-packaging: %s\n" % head)
        fh.write("git-dirty-files-at-packaging: %d\n" % dirty)
        fh.write("note: the working tree was dirty at packaging time; the\n"
                 "      authoritative identifier is the payload SHA-256\n"
                 "      (MANIFEST.sha256), not the git commit.\n")
        if derived:
            fh.write("derived-from: %s\n" % derived)
    print(vid, "(%d files)" % len(lines))


if __name__ == "__main__":
    main()
