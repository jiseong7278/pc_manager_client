"""generate_wxs.py - dist/PCInspectClient/ 파일 목록으로 dist_files.wxs 자동 생성

디렉토리 구조를 보존하는 WiX Fragment 파일 생성:
  - Fragment 1: 서브디렉토리 구조 (_internal 등)
  - Fragment 2: ComponentGroup "DistFileComponents"
"""

import os

DIST_DIR  = os.path.join("dist", "PCInspectClient")
ROOT_DIR_ID = "INSTALLFOLDER"
SKIP_FILES  = {"PCInspectClient.exe"}


def make_id(rel_path: str) -> str:
    return (rel_path
            .replace("\\", "_").replace("/", "_")
            .replace(".",  "_").replace("-", "_")
            .replace(" ",  "_").replace("+", "_"))


# ── 디렉토리 트리 수집 ────────────────────────────────────────────
class Node:
    def __init__(self, name: str, rel: str):
        self.name = name
        self.rel  = rel
        self.children: dict[str, "Node"] = {}
        self.files: list[tuple[str, str]] = []  # (rel_file, source_path)


root = Node(".", ".")

for dirpath, _, filenames in os.walk(DIST_DIR):
    rel_dir = os.path.relpath(dirpath, DIST_DIR)
    parts   = [] if rel_dir == "." else rel_dir.split(os.sep)

    node = root
    for i, part in enumerate(parts):
        if part not in node.children:
            node.children[part] = Node(part, os.path.join(*parts[: i + 1]))
        node = node.children[part]

    for fname in sorted(filenames):
        if rel_dir == "." and fname in SKIP_FILES:
            continue
        rel_file = os.path.relpath(os.path.join(dirpath, fname), DIST_DIR)
        source   = os.path.join("dist", "PCInspectClient", rel_file)
        node.files.append((rel_file, source))


# ── XML 생성 ─────────────────────────────────────────────────────
dir_lines:  list[str] = []
comp_lines: list[str] = []
file_count = 0


def emit_dir_tree(node: Node, indent: str) -> None:
    for child in sorted(node.children.values(), key=lambda n: n.name):
        cid = "Dir_" + make_id(child.rel)
        dir_lines.append(f'{indent}<Directory Id="{cid}" Name="{child.name}">')
        emit_dir_tree(child, indent + "  ")
        dir_lines.append(f'{indent}</Directory>')


def emit_components(node: Node, dir_id: str) -> None:
    global file_count
    for rel_file, source in node.files:
        fid = "File_" + make_id(rel_file)
        cid = "Comp_" + fid
        comp_lines.append(f'      <Component Id="{cid}" Directory="{dir_id}" Guid="*">')
        comp_lines.append(f'        <File Id="{fid}" Source="{source}" KeyPath="yes" />')
        comp_lines.append(f'      </Component>')
        file_count += 1
    for child in sorted(node.children.values(), key=lambda n: n.name):
        emit_components(child, "Dir_" + make_id(child.rel))


emit_dir_tree(root, "      ")
emit_components(root, ROOT_DIR_ID)

# ── WiX 파일 조립 ─────────────────────────────────────────────────
xml_lines = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">',
]

if dir_lines:
    xml_lines += [
        '  <Fragment>',
        f'    <DirectoryRef Id="{ROOT_DIR_ID}">',
        *dir_lines,
        '    </DirectoryRef>',
        '  </Fragment>',
    ]

xml_lines += [
    '  <Fragment>',
    '    <ComponentGroup Id="DistFileComponents">',
    *comp_lines,
    '    </ComponentGroup>',
    '  </Fragment>',
    '</Wix>',
]

with open("dist_files.wxs", "w", encoding="utf-8") as f:
    f.write("\n".join(xml_lines))

print(f"Generated dist_files.wxs with {file_count} components")
