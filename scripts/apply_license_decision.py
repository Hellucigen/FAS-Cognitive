# -*- coding: utf-8 -*-
# apply_license_decision.py — 许可决定(CCBY4.0+MIT)写入两包:
# README 版本块/License 段/sanitizer 引用、CITATION.cff version+license。
OLD_MAIN = "sha256:d725e5e108bb8a1b"   # original pkg, pre-license stamp
OLD_SAN  = "sha256:c6d986c89a4413fd"   # sanitized pkg, pre-license stamp
NEW_MAIN = "sha256:d725e5e108bb8a1b"
NEW_SAN  = "sha256:be68a9ddac9c0f53"

LICENSE_BLOCK = """## License

- **`code/` and `scripts/`**: MIT License (full text in `LICENSE`).
- **Everything else (`data/`, `preregistration/`, archives, docs)**:
  Creative Commons Attribution 4.0 International (CC BY 4.0; full legal
  code linked in `LICENSE`).
- Rationale and the alternatives considered: `LICENSE_OPTIONS.md`.
- Zenodo form: select license "CC BY 4.0" for the record; the split
  licensing is documented in `LICENSE`.
"""

def update_readme(pkg, vid, derived):
    p = pkg + "/README.md"
    s = open(p, encoding="utf-8", newline="").read()
    # 1) version block hashes
    for old in (OLD_MAIN, OLD_SAN):
        s = s.replace(old, vid)
    # 2) License section
    i = s.index("## License")
    j = s.index("## Routing campaign", i)
    s = s[:i] + LICENSE_BLOCK + "\n" + s[j:]
    # 3) sanitizer dangling reference
    s = s.replace(
        "see `code/scripts/sanitize_package.py`).",
        "the sanitizer script itself is the authors' private tool and is not redistributed, "
        "because its replacement table contains the original strings).")
    open(p, "w", encoding="utf-8", newline="").write(s)
    print("README updated:", pkg)

def update_cff(pkg, vid):
    p = pkg + "/CITATION.cff"
    s = open(p, encoding="utf-8", newline="").read()
    s = s.replace("version: 1.0.0+pkg.", "version: 1.0.0+pkg_OLD_")
    s = s.replace("version: 1.0.0+pkg_OLD_", "version: 1.0.0+pkg.%s" % vid.split(":")[1])
    s = s.replace('license: "[LICENSE-TO-BE-CHOSEN-SEE-LICENSE_OPTIONS.md]"',
                  'license: "CC-BY-4.0 (data, docs) & MIT (code) -- see LICENSE"')
    open(p, "w", encoding="utf-8", newline="").write(s)
    print("CITATION updated:", pkg)

update_readme("deliverables/zenodo_package", NEW_MAIN, None)
update_readme("deliverables/zenodo_package_sanitized", NEW_SAN, OLD_MAIN)
update_cff("deliverables/zenodo_package", NEW_MAIN)
update_cff("deliverables/zenodo_package_sanitized", NEW_SAN)
print("done")
