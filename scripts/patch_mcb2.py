# patch_mcb2.py — mc 反射接线（索引拼接法，免空白匹配）
s = open('app.py', encoding='utf-8').read()

# c1
c1 = 'has_action_result=bool(_web_results or _file_result or _eye_result))'
assert c1 in s, 'c1'
s = s.replace(c1, 'has_action_result=bool(_web_results or _file_result or _eye_result or _mc_action_result))', 1)

# c2: 强制回应条件 —— 定位 _dd.get("decision") 前插入 _mc_action_result
marker = 'or _dd.get("decision") in ("respond", "minimal")):'
assert s.count(marker) == 1, f'marker count {s.count(marker)}'
i = s.index(marker)
# 找该行行首
ls = s.rfind('\n', 0, i) + 1
line = s[ls:s.index('\n', i)]
line_has_mc = '_mc_action_result' in line
if not line_has_mc:
    s = s.replace(line, line.replace('or _dd.get', 'or _mc_action_result or _dd.get'), 1)

# c3: 元数据
c3 = '''        if _eye_result:
            response_data["eye"] = {'''
assert c3 in s, 'c3'
s = s.replace(c3, '''        if _mc_action_result:
            response_data["mc_action"] = _mc_action_result
        if _eye_result:
            response_data["eye"] = {''', 1)

open('app.py', 'w', encoding='utf-8').write(s)
import ast
ast.parse(s)
print('patch_mcb2 完成')
