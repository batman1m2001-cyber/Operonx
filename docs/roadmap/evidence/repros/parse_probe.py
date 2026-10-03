from operonx.providers.parsing import ExtractField, parse_and_extract

F = [ExtractField.from_string(s) for s in ["summary: str", "urgency: int", "ok: bool", "tags: list"]]
cases = {
    "xml_amp": ("xml", "<r><summary>Q&A broken</summary><urgency>2</urgency><ok>yes</ok><tags>a</tags></r>"),
    "xml_preamble": ("xml", "Here you go:\n<r><summary>s</summary><urgency>2</urgency><ok>no</ok><tags>a</tags></r>"),
    "xml_int_float": ("xml", "<r><summary>s</summary><urgency>2.5</urgency><ok>nah</ok><tags>a</tags></r>"),
    "json_fence_tail": ("json", '```json\n{"summary":"s","urgency":2,"ok":true,"tags":["a"]}\n```\nHope this helps'),
    "json_preamble": ("json", 'Result: {"summary":"s","urgency":2,"ok":true,"tags":["a"]}'),
    "xml_one_tag_list": ("xml", "<r><summary>s</summary><urgency>2</urgency><ok>1</ok><tags>a</tags></r>"),
    "xml_two_tag_list": ("xml", "<r><summary>s</summary><urgency>2</urgency><ok>1</ok><tags>a</tags><tags>b</tags></r>"),
}
for k, (p, t) in cases.items():
    print(k, parse_and_extract(t, p, F))
