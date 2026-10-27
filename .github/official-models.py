"""Points an app's model downloads (ATTRIBUTION.toml [[fetched]] entries with kind = "model") at the
copies inside the app's official release package, so the build doesn't depend on the model host.
The build still checks each file's SHA-256 against ATTRIBUTION.toml, so only the exact files pass.

usage: official-models.py <ATTRIBUTION.toml> <models folder from the official package>"""
import pathlib, re, sys

toml, models = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
text = toml.read_text(encoding='utf-8')
blocks = re.split(r'(?m)^(?=\[\[)', text)
out, changed = [], 0
for b in blocks:
    if b.startswith('[[fetched]]') and re.search(r'(?m)^kind\s*=\s*"model"', b):
        name = re.search(r'(?m)^file\s*=\s*"([^"]+)"', b).group(1)
        model, licence = models / name, models / f'{name}.LICENCE.txt'
        if model.is_file():
            b = re.sub(r'(?m)^url\s*=\s*"[^"]*"', f'url = "{model.resolve().as_uri()}"', b)
            changed += 1
        if licence.is_file():
            b = re.sub(r'(?m)^licence_url\s*=\s*"[^"]*"', f'licence_url = "{licence.resolve().as_uri()}"', b)
        print(f'{name}: {"official copy" if model.is_file() else "not in the package, left as is"}')
    out.append(b)
toml.write_text(''.join(out), encoding='utf-8')
print(f'{changed} model(s) taken from the official release')
