"""generate_wxs.py - dist/PCInspectClient/ 파일 목록으로 dist_files.wxs 자동 생성"""

import os

dist_dir = os.path.join("dist", "PCInspectClient")
components = []

for root, dirs, files in os.walk(dist_dir):
    for fname in files:
        if fname == "PCInspectClient.exe":
            continue
        full_path = os.path.join(root, fname)
        rel = os.path.relpath(full_path, dist_dir)
        file_id = "File_" + rel.replace("\\", "_").replace(".", "_").replace("-", "_")
        comp_id = "Comp_" + file_id
        source  = os.path.join("dist", "PCInspectClient", rel)
        components.append(f'      <Component Id="{comp_id}" Guid="*">')
        components.append(f'        <File Id="{file_id}" Source="{source}" KeyPath="yes" />')
        components.append(f'      </Component>')

lines = ['<Include xmlns="http://wixtoolset.org/schemas/v4/wxs">']
lines.extend(components)
lines.append('</Include>')

with open("dist_files.wxs", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Generated dist_files.wxs with {len(components) // 3} components")
