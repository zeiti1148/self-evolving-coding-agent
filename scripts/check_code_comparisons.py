"""只读验收已结束任务的代码对比链接。

示例：python scripts/check_code_comparisons.py --task-id UUID --base-url http://127.0.0.1:8003
检查记录内容、校验值及接口一致性；修复正确性仍以独立测试为准。
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import build_opener, ProxyHandler
from uuid import UUID, uuid4


def check_tasks(base_url, task_ids):
    opener = build_opener(ProxyHandler({}))
    def get(path):
        with opener.open(base_url.rstrip('/') + path, timeout=15) as response:
            raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError('接口响应超过验收脚本的大小限制。')
            return json.loads(raw)
    cases = []
    for identifier in task_ids:
        task_id = str(UUID(identifier))
        prefix = f'/api/tasks/{task_id}'
        task = get(prefix)
        if task['status'] not in {'succeeded', 'failed', 'stopped'}:
            raise ValueError('请等待任务结束后再检查代码对比。')
        index = get(prefix + '/code-changes')
        assert index['task_id'] == task_id and index['files'], '没有可检查的代码改动。'
        assert index['omitted_files'] == 0, '部分改动超出展示限制。'
        files = []
        for entry in index['files']:
            assert entry['available'], entry['unavailable_reason']
            data = get(prefix + '/code-diff?' + urlencode({'path': entry['path']}))
            assert data['task_id'] == task_id and data['path'] == entry['path']
            assert data['mode'] == index['mode']
            for version in ('before', 'after'):
                content, digest = data[version + '_content'], data[version + '_sha256']
                assert digest == entry[version + '_sha256']
                assert (content is None) == (digest is None)
                if content is not None:
                    assert hashlib.sha256(content.encode('utf-8')).hexdigest() == digest
            assert data['before_content'] != data['after_content'], '返回了没有变化的文件。'
            lines = [line for hunk in data['hunks'] for line in hunk['lines']]
            assert sum(line['kind'] == 'added' for line in lines) == data['additions']
            assert sum(line['kind'] == 'removed' for line in lines) == data['deletions']
            files.append({key: data[key] for key in ('path', 'change_type', 'before_sha256', 'after_sha256', 'additions', 'deletions', 'format_only')})
        assert get(prefix) == task, '查看对比时任务记录发生变化。'
        cases.append({'task_id': task_id, 'status': task['status'], 'mode': index['mode'], 'files': files, 'task_unchanged': True})
    return {'passed': True, 'checked_at': datetime.now(timezone.utc).isoformat(), 'cases': cases}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8003', help='后端接口地址')
    parser.add_argument('--task-id', action='append', required=True, help='任务编号，可多次指定')
    parser.add_argument('--output', type=Path, default=Path('.cache/code-comparisons') / f'{uuid4().hex}.json', help='报告保存路径，须为新文件')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出文件已存在，请选择新的报告路径。')
    result = check_tasks(args.base_url, args.task_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"通过：{len(result['cases'])} 条任务，报告：{args.output}")
