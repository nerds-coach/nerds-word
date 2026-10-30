#!/usr/bin/env python3
"""NERDS names for a Craft app's source tree, before it is built.

    python3 nerds-rename.py <app checkout> <nerds.json> [--repo OWNER/NAME] [--i18n DIR] [--brand DIR]
                            [--report FILE]

Shows every Craft app under its NERDS name (PhotoCraft -> NERDS Photo) wherever a person reads it:
window titles, menus, About, messages, the translations, the Windows installer (product, Start menu
and Apps list) and the Mac app bundle (its name in Finder and the Dock, and the disk image). The
publisher becomes NERDS Craft. An app that checks for its own updates looks at this repository's
releases (--repo, else $GITHUB_REPOSITORY) instead of upstream's.

What it leaves alone, so files, settings and other programs keep working: anything that is an
identifier rather than words to read (data folders such as PhotoCraftData or
"Application Support/PhotoCraft", file names, file-format markers, registry keys, bundle ids,
the executable's name, byte strings), test code, and copyright lines. A name standing alone in
a string ("PhotoCraft") is only changed where the code shows it (a window title, a menu, a
label); everywhere else it may be an identifier and stays.

nerds.json may list `keep` (["path: text in the line", ...]: literals on matching lines stay as
they are; the path may be `*`, and text starting with `=` must be the whole line) and `patches` ([{"file", "old", "new"}, ...]: exact replacements, each expected once),
and `brand` ({"docs/brand/x.png": "x.png", ...}: files copied from --brand over the app's own).
Everything changed is written to the report; problems are reported as warnings and never stop
the build.
"""
import bisect
import json
import os
import re
import shutil
import string
import subprocess
import sys

# Every Craft app, upstream name -> NERDS name. Each app shows its siblings' NERDS names too.
NAMES = {
    "PhotoCraft": "NERDS Photo",
    "VectorCraft": "NERDS Vector",
    "FilmCraft": "NERDS Film",
    "LightCraft": "NERDS Light",
    "PdfCraft": "NERDS PDF",
    "EffectCraft": "NERDS Effects",
    "DesignCraft": "NERDS Layout",
    "FigCraft": "NERDS Proto",
    "WordCraft": "NERDS Word",
    "SoundCraft": "NERDS Sound",
    "DeckCraft": "NERDS Deck",
    "CADCraft": "NERDS CAD",
    "GridCraft": "NERDS Grid",
}
PUBLISHERS = ("Learning Machines LLC",)
NERDS_PUBLISHER = "NERDS Craft"
NAME_RE = re.compile("|".join(sorted(map(re.escape, NAMES), key=len, reverse=True)))

WORD = set(string.ascii_letters + string.digits + "_")
# A name right after one of these is part of an identifier, a path or a marker: X-PhotoCraft,
# Software\PhotoCraft, %PhotoCraft, Name=PhotoCraft, <PhotoCraftProject, URL:PhotoCraft.
SKIP_BEFORE = WORD | set("./\\%=@#$&<:-")

report = []
warnings = []
# The app's own update check and release page: this repository's releases instead of upstream's.
URLS = []


def warn(msg):
    warnings.append(msg)
    print("::warning::" + msg if os.environ.get("GITHUB_ACTIONS") else "warning: " + msg, file=sys.stderr)


def occurrence_ok(t, s, e, suffixes=()):
    """Whether the name at t[s:e] is a word to read (and not part of an identifier)."""
    for suf in suffixes:  # a file named after the app: NERDS Photo.app, NERDS Photo.lnk
        end = e + len(suf)
        if t.startswith(suf, e) and (end >= len(t) or t[end] not in WORD):
            return True
    b = t[s - 1] if s > 0 else ""
    if s >= 2 and t[s - 2] == "\\" and t[s - 1] in 'nrt0"\'':
        b = " "  # after an escape such as \n or \"
    if b and b in SKIP_BEFORE:
        return False
    a = t[e] if e < len(t) else ""
    a2 = t[e + 1] if e + 1 < len(t) else ""
    if not a:
        return True
    if a in WORD:
        return False
    if a == "." and a2 in WORD:  # a file name: PhotoCraft.portable, PhotoCraft.icns
        return False
    if a == "\\" and a2 in 'nrt0"\'':
        return True  # before an escape such as \n
    if a == "&" and re.match(r"&(quot|amp|apos|lt|gt|#\d+|#x[0-9a-fA-F]+);", t[e:]):
        return True  # before an XML entity: "Edit with PdfCraft&quot;"
    if a in "/\\=<%@#$&":
        return False
    if a == ":" and a2 and not a2.isspace():
        return False
    if a == "-" and (a2 == "" or a2 in string.ascii_lowercase or a2 in string.digits):
        return False  # PhotoCraft-update-check; but PhotoCraft-Website (German) is words
    return True


def rename_text(t, suffixes=()):
    """Every readable occurrence of a Craft name in t, replaced."""
    out, last = [], 0
    for m in NAME_RE.finditer(t):
        if occurrence_ok(t, m.start(), m.end(), suffixes):
            out.append(t[last:m.start()])
            out.append(NAMES[m.group(0)])
            last = m.end()
    out.append(t[last:])
    return "".join(out)


# ---- Rust -----------------------------------------------------------------------------------

RAW = re.compile(r'(b|c)?r(#*)"')
STR = re.compile(r'(b|c)?"')
CHAR = re.compile(r"'(\\(x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]+\}|.)|[^\\'\n])'")
BCHAR = re.compile(r"b'(\\(x[0-9a-fA-F]{2}|.)|[^\\'])'")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def literals(src, comments=None):
    """Each string literal in Rust source: start/end, body range, prefix (b, c, r#), and the
    macros and attributes it is inside. Comments, char literals and lifetimes are skipped."""
    i, n = 0, len(src)
    stack, pending_macro, pending_attr, last_ident = [], None, False, None
    while i < n:
        c = src[i]
        if c == "/" and src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            if comments is not None:
                comments.append((i, j))
            i = j
            continue
        if c == "/" and src.startswith("/*", i):
            st, depth, i = i, 1, i + 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth += 1
                    i += 2
                elif src.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if comments is not None:
                comments.append((st, i))
            continue
        m = RAW.match(src, i)
        if m and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            close = '"' + m.group(2)
            j = src.find(close, m.end())
            if j < 0:
                raise ValueError("unterminated raw string at %d" % i)
            yield dict(start=i, end=j + len(close), prefix=src[i:m.end()], body=(m.end(), j), stack=list(stack))
            i, last_ident = j + len(close), None
            continue
        m = STR.match(src, i)
        if m and (m.group(1) is None or i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            j = m.end()
            while True:
                if j >= n:
                    raise ValueError("unterminated string at %d" % i)
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == '"':
                    break
                j += 1
            yield dict(start=i, end=j + 1, prefix=src[i:m.end()], body=(m.end(), j), stack=list(stack))
            i, last_ident = j + 1, None
            continue
        if c == "'":
            m = CHAR.match(src, i)
            i = m.end() if m else i + 1
            continue
        if c == "b" and src.startswith("b'", i):
            m = BCHAR.match(src, i)
            if m:
                i = m.end()
                continue
        m = IDENT.match(src, i)
        if m:
            last_ident, i = m.group(0), m.end()
            continue
        if c == "!" and last_ident and not src.startswith("!=", i):
            pending_macro, i = last_ident, i + 1
            continue
        if c == "#" and (src.startswith("#[", i) or src.startswith("#![", i)):
            pending_attr = True
            i += 1 if src[i + 1] == "[" else 2
            continue
        if c in "([{":
            if pending_attr and c == "[":
                stack.append(("attr", None))
                pending_attr = False
            else:
                stack.append((c, pending_macro))
            pending_macro, last_ident, i = None, None, i + 1
            continue
        if c in ")]}":
            if stack:
                stack.pop()
            pending_macro, last_ident, i = None, None, i + 1
            continue
        if not c.isspace():
            pending_macro = None
            if c != ":":
                last_ident = None
        i += 1


def test_ranges(src):
    """Byte ranges of #[cfg(test)] items, ignoring text in strings and comments."""
    comments = []
    skip = sorted([(d["start"], d["end"]) for d in literals(src, comments)] + comments)
    jump = dict(skip)
    starts = [a for a, _ in skip]

    def masked(pos):
        k = bisect.bisect_right(starts, pos) - 1
        return k >= 0 and pos < skip[k][1]

    out = []
    for m in re.finditer(r"#\[cfg\(test\)\]", src):
        if masked(m.start()):
            continue
        p, depth = m.end(), 0
        while p < len(src):
            if p in jump:
                p = jump[p]
                continue
            ch = src[p]
            if ch == ";" and depth == 0:
                break
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            p += 1
        out.append((m.start(), p + 1))
    return out


# Where a name standing alone in a string is shown to people ({q} is the quoted name).
UI_CONTEXTS = [
    r"\.with_title\(\s*{q}",
    r"\.set_title\(\s*{q}",
    r"Submenu::new\(\s*{q}",
    r"\bAPP_NAME\s*:\s*&(?:'static\s+)?str\s*=\s*{q}",
    r"RichText::new\(\s*(?:crate::)?(?:tl!\(\s*|(?:crate::)?i18n::tr\((?:[^,()]*,\s*)?)?{q}",
    r"\.heading\(\s*(?:crate::)?(?:tl!\(\s*)?{q}",
    r"\.set\(\s*\"(?:ProductName|FileDescription)\"\s*,\s*{q}",
    r"menu_button\(\s*{q}",
    r"layout_no_wrap\(\s*(?:(?:crate::)?tl!\(\s*)?{q}",
    r"\btl!\(\s*{q}\s*\)",
    r"\bi18n::tr\((?:[^,()]*,\s*)?{q}\s*\)",
    r"\.text\(.*,\s*(?:(?:crate::)?tl!\(\s*)?{q}\s*\)?\s*,\s*(?:Tokens::|egui::FontId|theme::|crate::theme::|t\.semibold|semibold|bold)",
    r"install_app_menu\([^,]+,\s*{q}\s*\)",
    r"ui\.label\(\s*{q}\s*\)",
    r"else\s*\{\s*{q}\s*\}",
    r"unwrap_or_else\(\|\|\s*{q}\.(?:into|to_string|to_owned)\(\)\)",
    r"map_or_else\(\|\|\s*{q}\.(?:into|to_string|to_owned)\(\)",
    r"None\s*=>\s*{q}\.(?:into|to_string|to_owned)\(\)",
    r"\(\s*\"[a-z]+craft\"\s*,\s*{q}\s*,",
    r"\(\s*{q}\s*,\s*\"[a-z]+craft\"\s*\)",
    r"^\s*{q}\.to_string\(\)\s*$",
    r"Some\(\(\s*{q}\.into\(\)\s*,",
]


def is_test_file(path):
    parts = path.split("/")
    base = parts[-1]
    if any(p in ("tests", "benches", "examples", "fuzz", "xtask") for p in parts[:-1]):
        return True
    if re.search(r"-web(-boot)?$", parts[1]) if len(parts) > 2 and parts[0] == "apps" else False:
        return True  # the browser builds aren't shipped here
    return bool(re.match(r"^(tests?\.rs|tests?_.*\.rs|.*_tests?\.rs|test_.*\.rs)$", base))


class Keep:
    def __init__(self, entries):
        self.entries = []
        for e in entries:
            path, _, text = e.partition(":")
            self.entries.append((path.strip(), text.strip()))
        self.used = set()

    def __call__(self, path, line):
        """Whether `line` of `path` is to be kept: an entry's path is a file or `*` (any file), and
        its text is part of the line, or with a leading `=`, the whole line (spaces trimmed)."""
        for i, (p, t) in enumerate(self.entries):
            hit = line.strip() == t[1:].strip() if t.startswith("=") else t in line
            if p in (path, "*") and hit:
                self.used.add(i)
                return True
        return False


def rename_rust(path, src, keep, catalog):
    """Rust source with the names in its strings replaced; logs each change."""
    tr = test_ranges(src)
    tstarts = [a for a, _ in tr]
    out, last, lines = [], 0, None
    for d in literals(src):
        s, e = d["body"]
        body = src[s:e]
        if not NAME_RE.search(body) and not any(p in body for p in PUBLISHERS) and "ArtCraft" not in body \
                and not any(u in body for u, _ in URLS):
            continue
        k = bisect.bisect_right(tstarts, d["start"]) - 1
        if k >= 0 and d["start"] < tr[k][1]:
            continue
        if d["prefix"].startswith(("b", "c")):
            continue
        macros = [name for kind, name in d["stack"] if name]
        if any(m in ("include_str", "include_bytes", "include", "env", "option_env", "concat") for m in macros):
            continue
        if "Copyright" in body or "©" in body or "(c)" in body:
            continue
        if lines is None:
            lines = src.split("\n")
        ln = src.count("\n", 0, d["start"])
        line = lines[ln]
        if keep(path, line):
            report.append(f"kept  {path}:{ln + 1}  {body[:100]!r}  (nerds.json keep)")
            continue
        new = body
        if body in PUBLISHERS:
            new = NERDS_PUBLISHER
        elif body == "ArtCraft" and re.search(r"\"CompanyName\"\s*,\s*\"ArtCraft\"", line):
            new = NERDS_PUBLISHER
        elif body in NAMES:
            q = '"' + re.escape(body) + '"'
            if catalog or any(re.search(c.replace("{q}", q), line) for c in UI_CONTEXTS):
                new = NAMES[body]
            else:
                report.append(f"kept  {path}:{ln + 1}  {line.strip()[:140]}")
                continue
        else:
            new = rename_text(body)
        for old_url, new_url in URLS:
            new = new.replace(old_url, new_url)
        if new != body:
            report.append(f"rust  {path}:{ln + 1}  {body[:90]!r} -> {new[:90]!r}")
            out.append(src[last:s])
            out.append(new)
            last = e
    out.append(src[last:])
    return "".join(out)


# The apps' shared updater (craft-update, a git dependency) knows each app by a constant
# (craft_update::suite::PHOTOCRAFT) holding its name and upstream repository. Each use becomes
# that constant with the NERDS name and repository, so an app looks for its updates here and
# names itself NERDS Photo in the update prompt.
SUITE_CONSTS = {name.upper(): name for name in NAMES}
NERDS_REPOS = {
    "PhotoCraft": "photo", "VectorCraft": "vector", "FilmCraft": "film", "LightCraft": "light",
    "PdfCraft": "pdf", "EffectCraft": "effects", "DesignCraft": "layout", "FigCraft": "proto",
    "WordCraft": "word", "SoundCraft": "sound", "DeckCraft": "deck", "CADCraft": "cad", "GridCraft": "grid",
}
OWNER = []


def nerds_app(const):
    upstream = SUITE_CONSTS[const]
    return (f'craft_update::App {{ name: "{NAMES[upstream]}", repo: "{OWNER[0]}/nerds-{NERDS_REPOS[upstream]}", '
            f'..craft_update::suite::{const} }}')


def rename_updater_app(path, src):
    if not OWNER or "craft_update" not in src:
        return src

    def use_line(m):
        report.append(f"rust  {path}: craft_update::suite::{m.group(2)} -> the NERDS repository")
        return f"{m.group(1)}#[allow(dead_code)]\n{m.group(1)}const {m.group(2)}: craft_update::App = {nerds_app(m.group(2))};"

    consts = "|".join(SUITE_CONSTS)
    src = re.sub(rf"^([ \t]*)use craft_update::suite::({consts});", use_line, src, flags=re.M)

    def path_use(m):
        report.append(f"rust  {path}: craft_update::suite::{m.group(1)} -> the NERDS repository")
        return nerds_app(m.group(1))
    return re.sub(rf"(?<!\.\.)craft_update::suite::({consts})\b", path_use, src)


# ---- translation catalogs -------------------------------------------------------------------

QUOTED = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def rename_catalog_tsv(text):
    out = []
    for line in text.split("\n"):
        if line.startswith("#") or "\t" not in line:
            out.append(line)
            continue
        cols = line.split("\t")
        cols[1:] = [NAMES.get(c, None) or rename_text(c) for c in cols[1:]]
        out.append("\t".join(cols))
    return "\n".join(out)


def rename_catalog_quoted(text):
    """TOML and JSON catalogs: every quoted key and value."""
    def one(m):
        body = m.group(1)
        return '"' + (NAMES.get(body) or rename_text(body)) + '"'
    out = []
    for line in text.split("\n"):
        out.append(line if line.lstrip().startswith("#") else QUOTED.sub(one, line))
    return "\n".join(out)


# ---- packaging ------------------------------------------------------------------------------

PLIST_KEYS = re.compile(r"^(CFBundleName|CFBundleDisplayName|CFBundleTypeName|UTTypeDescription|NS\w+UsageDescription)$")


def rename_plist(text):
    def one(m):
        key, value = m.group(2), m.group(4)
        if not PLIST_KEYS.match(key):
            return m.group(0)
        return m.group(1) + m.group(3) + rename_text(value, (".app",)) + m.group(5)
    return re.sub(r"(<key>(\w+)</key>)(\s*<string>)([^<]*)(</string>)", one, text)


def rename_publisher(text):
    for p in PUBLISHERS:
        text = re.sub(r"([\"'])" + re.escape(p) + r"\1", lambda m: m.group(1) + NERDS_PUBLISHER + m.group(1), text)
    return text


def rename_windows(text):
    """WiX sources and their localisation: readable names (and the Start menu shortcut's file
    name), the publisher. Registry keys, ids and paths stay."""
    text = rename_publisher(text)
    # The name the app is registered under for Default apps is an id its code uses
    # (ms-settings:defaultapps?registeredAppMachine=X), so those lines stay.
    return "\n".join(line if "RegisteredApplications" in line else rename_text(line, (".lnk",))
                     for line in text.split("\n"))


PS_STRING = re.compile(r"'(?:[^'\n]|'')*'|\"(?:[^\"`\n]|`.)*\"")


def rename_powershell(text):
    """PowerShell scripts: only text inside quotes, and never a name standing alone in quotes,
    because a name in code (Add-Type -Namespace PhotoCraft, 'PhotoCraft' as a folder) is an
    identifier. Messages and the labels the installer tests compare ('Edit with PdfCraft') change
    as they do in the installer."""
    text = rename_publisher(text)

    def one(m):
        q = m.group(0)
        body = q[1:-1]
        if body in NAMES or "RegisteredApplications" in body:
            return q
        return q[0] + rename_text(body, (".lnk",)) + q[-1]
    return "\n".join(line if line.lstrip().startswith("#") else PS_STRING.sub(one, line) for line in text.split("\n"))


def rename_install_policy(text):
    """install-policy.json: the registry rows the policy check expects, changed as the installer's
    are (FriendlyAppName "PhotoCraft" -> "NERDS Photo"). Its `name` stays: it is the registry key
    and folder name the installer keeps."""
    policy = json.loads(text)
    for row in policy.get("registry", []):
        if len(row) >= 4 and "RegisteredApplications" not in row[1]:
            row[2], row[3] = rename_text(row[2], (".lnk",)), rename_text(row[3], (".lnk",))
    return json.dumps(policy, indent=2, ensure_ascii=False) + "\n"


def rename_macos_script(text):
    """package.sh and the DMG layout script: the bundle name (X.app) and the volume name ("X");
    the executable (Contents/MacOS/X), icon file and work files keep their names."""
    def one(m):
        name, after = m.group(0), text[m.end():m.end() + 4]
        before = text[m.start() - 1] if m.start() else ""
        if after == ".app":
            return NAMES[name]
        if before == '"' and text[m.end():m.end() + 1] == '"':
            return NAMES[name]
        return name
    return NAME_RE.sub(one, text)


# ---- driver ---------------------------------------------------------------------------------

def git_files(root, *patterns):
    r = subprocess.run(["git", "-C", root, "ls-files", "-z", "--", *patterns], capture_output=True, text=True, check=True)
    return [f for f in r.stdout.split("\0") if f]


def edit(root, rel, fn, kind):
    path = os.path.join(root, rel)
    try:
        with open(path, encoding="utf-8", newline="") as f:
            old = f.read()
    except (OSError, UnicodeDecodeError) as e:
        warn(f"couldn't read {rel}: {e}")
        return False
    try:
        new = fn(old)
    except ValueError as e:
        warn(f"{rel} left as it is: {e}")
        return False
    if new != old:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        if kind:
            n = sum(1 for a, b in zip(old.split("\n"), new.split("\n")) if a != b)
            report.append(f"{kind:5s} {rel}  ({n} lines)")
        return True
    return False


def main(argv):
    args, opts = [], {}
    it = iter(argv)
    for a in it:
        if a.startswith("--"):
            opts[a[2:]] = next(it)
        else:
            args.append(a)
    root, cfg_path = args
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    keep = Keep(cfg.get("keep", []))
    repo = opts.get("repo") or os.environ.get("GITHUB_REPOSITORY", "")
    upstream = cfg.get("upstream", "storytold/" + cfg["app"])
    if repo and "/" in repo:
        OWNER.append(repo.split("/")[0])
    if repo and repo != upstream:
        URLS.extend([(f"github.com/repos/{upstream}/releases", f"github.com/repos/{repo}/releases"),
                     (f"github.com/{upstream}/releases", f"github.com/{repo}/releases")])
    has_name = re.compile("|".join(map(re.escape, list(NAMES) + list(PUBLISHERS) + ["ArtCraft", "craft_update::suite"] + [u for u, _ in URLS])))

    def mentions(rel):
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
                return bool(has_name.search(f.read()))
        except OSError:
            return False

    # Rust: the apps and crates (catalogs written as Rust tables count as catalogs).
    for rel in git_files(root, "*.rs"):
        if is_test_file(rel) or not mentions(rel):
            continue
        catalog = bool(re.search(r"/i18n(/|\.rs$)|/locales?/", rel))
        edit(root, rel, lambda s, rel=rel, c=catalog: rename_updater_app(rel, rename_rust(rel, s, keep, c)), "")

    # Translation catalogs: the app's own and craft-i18n's.
    for rel in git_files(root, "*.tsv"):
        if "/i18n/" in rel or "/locales/" in rel:
            edit(root, rel, rename_catalog_tsv, "i18n")
    for rel in git_files(root, "*/locales/*.toml", "*/locales/*.json"):
        edit(root, rel, rename_catalog_quoted, "i18n")
    i18n = opts.get("i18n")
    if i18n and os.path.isdir(os.path.join(i18n, "locales")):
        app = cfg["app"]
        for lang in sorted(os.listdir(os.path.join(i18n, "locales"))):
            rel = os.path.join("locales", lang, app + ".tsv")
            if os.path.isfile(os.path.join(i18n, rel)):
                edit(i18n, rel, rename_catalog_tsv, "i18n")

    # Packaging: Windows installer, Mac bundle and disk image.
    for rel in git_files(root, "packaging/windows/*"):
        if rel.endswith((".wxs", ".wxl", ".txt", ".rtf")):
            edit(root, rel, rename_windows, "win")
        elif rel.endswith(".ps1") and not rel.endswith("sign.ps1"):
            edit(root, rel, rename_powershell, "win")
        elif rel.endswith("install-policy.json"):
            edit(root, rel, rename_install_policy, "win")
    for rel in git_files(root, "packaging/macos/*.plist.in", "packaging/macos/Info.plist"):
        edit(root, rel, rename_plist, "mac")
    for rel in git_files(root, "packaging/macos/package.sh", "packaging/macos/dmg/generate.py"):
        edit(root, rel, rename_macos_script, "mac")

    # Exact replacements and brand files for this app.
    for p in cfg.get("patches", []):
        path = os.path.join(root, p["file"])
        try:
            text = open(path, encoding="utf-8", newline="").read()
        except OSError as e:
            warn(f"patch: {p['file']}: {e}")
            continue
        old_text, new_text = p["old"], p["new"]
        if old_text not in text and "\r\n" in text:  # a checkout with Windows line endings
            old_text, new_text = old_text.replace("\n", "\r\n"), new_text.replace("\n", "\r\n")
        n = text.count(old_text)
        if n != 1:
            warn(f"patch: {p['file']}: expected the text once, found it {n} times: {p['old'][:80]!r}")
            continue
        open(path, "w", encoding="utf-8", newline="").write(text.replace(old_text, new_text))
        report.append(f"patch {p['file']}: {p['old'][:60]!r} -> {p['new'][:60]!r}")
    brand = opts.get("brand")
    for dest, src in cfg.get("brand", {}).items():
        if not brand or not os.path.isfile(os.path.join(brand, src)):
            warn(f"brand file {src} missing")
            continue
        if not os.path.exists(os.path.join(root, dest)):
            warn(f"brand: {dest} isn't in this version of the app")
            continue
        shutil.copyfile(os.path.join(brand, src), os.path.join(root, dest))
        report.append(f"brand {dest} <- {src}")

    for i, (p, t) in enumerate(keep.entries):
        if i not in keep.used:
            warn(f"keep entry matched nothing: {p}: {t}")

    changed = sum(1 for r in report if r.startswith(("rust", "patch", "brand"))) + sum(1 for r in report if r.startswith(("i18n", "win", "mac")))
    summary = f"NERDS names: {changed} changes, {sum(1 for r in report if r.startswith('kept'))} kept, {len(warnings)} warnings"
    print(summary)
    if "report" in opts:
        with open(opts["report"], "w", encoding="utf-8") as f:
            f.write(summary + "\n\n")
            for w in warnings:
                f.write("WARNING " + w + "\n")
            f.write("\n".join(report) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
