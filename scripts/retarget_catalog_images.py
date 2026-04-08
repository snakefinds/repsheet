import html
import json
import pathlib
import re


HTML_PATH = pathlib.Path(r"C:\Users\alan\Downloads\Finds — Catalog.html")
DATA_PATH = pathlib.Path(r"C:\Users\alan\Downloads\Site\data.json")


def main():
    source = HTML_PATH.read_text(encoding="utf-8", errors="ignore")
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    by_title = {
        str(it.get("title", "")).strip(): str(it.get("img", "")).strip()
        for it in data.get("items", [])
        if str(it.get("title", "")).strip()
    }

    img_pat = re.compile(
        r'(<img\s+[^>]*?src=")([^"]+)("[^>]*?alt=")([^"]+)("[^>]*>)',
        re.I,
    )

    replaced = 0

    def repl(m):
        nonlocal replaced
        title = html.unescape(m.group(4)).strip()
        img = by_title.get(title, "")
        if img.startswith("images/"):
            replaced += 1
            new_src = "./Site/" + img.replace("\\", "/")
            return m.group(1) + new_src + m.group(3) + m.group(4) + m.group(5)
        return m.group(0)

    updated = img_pat.sub(repl, source)
    HTML_PATH.write_text(updated, encoding="utf-8")
    print(f"updated_img_tags={replaced}")


if __name__ == "__main__":
    main()
