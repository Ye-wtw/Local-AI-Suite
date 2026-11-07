# -*- coding: utf-8 -*-
import io, json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ('2025-03', '2025-04')
N = chr(10)
HEAD = ('{question}' + N + N + '请用 Python 作答。' + N + N
        + '接口要求(评测程序按此调用,不要用 input()/print()):' + N
        + '    def solve(data: str) -> str:' + N
        + 'data 是完整标准输入,返回值应等于应有的标准输出。' + N)
TEST = ('def check(solve):' + N
        + '    cases = {cases!r}' + N
        + '    for i, (inp, want) in enumerate(cases, 1):' + N
        + '        got = solve(inp)' + N
        + '        if not isinstance(got, str):' + N
        + '            got = str(got)' + N
        + '        if got.strip() != want.strip():' + N
        + '            raise AssertionError(\'case %d input=%r want=%r got=%r\' % (i, inp, want, got))' + N)

def main():
    src = os.path.join(ROOT, 'datasets', 'public', 'livecodebench.jsonl')
    dst = os.path.join(ROOT, 'datasets', 'full', 'D4_full.jsonl')
    lcb = []
    for line in io.open(src, encoding='utf-8'):
        if not line.strip(): continue
        o = json.loads(line)
        if o.get('difficulty') != 'hard': continue
        if (o.get('contest_date') or '')[:7] not in MONTHS: continue
        cases = [[t.get('input') or '', t.get('output') or '']
                 for t in (o.get('public_tests') or [])
                 if (t.get('input') or '').strip() or (t.get('output') or '').strip()]
        if not cases: continue
        lcb.append({'id': o['id'], 'dim': 'D4', 'subcat': 'public-livecodebench',
                    'source': 'public:livecodebench', 'contest_date': o.get('contest_date'),
                    'platform': o.get('platform'), 'difficulty': o.get('difficulty'),
                    'notes': 'LCB hard %s;exec' % (o.get('contest_date') or '')[:10],
                    'prompt': HEAD.replace('{question}', (o.get('question') or '').strip()),
                    'scoring': 'exec', 'entry_point': 'solve',
                    'test': TEST.replace('{cases}', repr(cases))})
    existing = [json.loads(l) for l in io.open(dst, encoding='utf-8') if l.strip()]
    seen = set(x.get('id') for x in existing)
    added = [x for x in lcb if x['id'] not in seen]
    with io.open(dst, 'w', encoding='utf-8', newline=N) as f:
        for o in existing + added:
            f.write(json.dumps(o, ensure_ascii=False) + N)
    print('原有 D4        : %d' % len(existing))
    print('新增 LCB(hard) : %d' % len(added))
    print('最终 D4        : %d' % (len(existing) + len(added)))

main()