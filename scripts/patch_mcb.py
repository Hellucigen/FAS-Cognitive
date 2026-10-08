# patch_mcb.py — mc 反射接线三处编辑
s = open('app.py', encoding='utf-8').read()

c1 = 'has_action_result=bool(_web_results or _file_result or _eye_result))'
assert c1 in s, 'c1'
s = s.replace(c1, 'has_action_result=bool(_web_results or _file_result or _eye_result or _mc_action_result))', 1)

c2 = '''                    and (_web_results or _file_result or _eye_result
                         or _dd.get("decision") in ("respond", "minimal")):'''
assert c2 in s, 'c2'
s = s.replace(c2, '''                    and (_web_results or _file_result or _eye_result or _mc_action_result
                         or _dd.get("decision") in ("respond", "minimal")):''', 1)

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
print('5-6 编辑完成')
