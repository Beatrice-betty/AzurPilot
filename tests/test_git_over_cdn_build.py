"""验证 JS 双历史构建产物与 Python 镜像及真实客户端兼容。"""

import base64
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from deploy.git_over_cdn.client import GitOverCdnClient
from tests.test_mirror_history import mirror


ROOT = Path(__file__).resolve().parents[1]
BUILDER = (ROOT / '.github/scripts/build_git_over_cdn_eo_esa.mjs').as_uri()


def git(cwd, *args, data=None):
    return subprocess.run(
        ['git', *args], cwd=cwd, input=data, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


@contextmanager
def restored_cwd():
    previous = Path.cwd()
    try:
        yield
    finally:
        os.chdir(previous)


class TestGitOverCdnBuild(unittest.TestCase):
    def test_two_sha_packs_update_real_repositories_and_match_python(self):
        previous_cwd = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory, restored_cwd():
                root = Path(directory)
                source = root / 'source'
                git(root, 'init', str(source))
                stream = bytearray()
                for index in range(9):
                    message = f'测试提交 {index}\n'.encode('utf-8')
                    content = f'文件内容 {index}\n'.encode('utf-8')
                    stream.extend(
                        f'commit refs/heads/master\nmark :{index + 1}\n'
                        f'committer 测试 <test@example.invalid> {1700000000 + index} +0800\n'
                        f'data {len(message)}\n'.encode('utf-8') + message
                    )
                    if index:
                        stream.extend(f'from :{index}\n'.encode())
                    stream.extend(f'M 100644 inline file.txt\ndata {len(content)}\n'.encode() + content + b'\n')
                git(source, 'fast-import', '--quiet', data=bytes(stream))
                baseline = git(source, 'rev-parse', 'master~5').decode()
                old = git(source, 'rev-parse', 'master~2').decode()
                latest = git(source, 'rev-parse', 'master').decode()
                tree = git(source, 'rev-parse', 'master^{tree}')
                side_raw = (
                    b'tree ' + tree + b'\nparent ' + old.encode()
                    + '\nauthor 测试 <test@example.invalid> 1700000010 +0800\n'.encode('utf-8')
                    + b'committer Test <test@example.invalid> 1700000010 +0800\n'
                    b'gpgsig signature\n continuation\n\nside\n'
                )
                side = git(source, 'hash-object', '-t', 'commit', '-w', '--stdin', data=side_raw)
                merge_raw = side_raw.replace(
                    b'parent ' + old.encode(), b'parent ' + latest.encode() + b'\nparent ' + side,
                ).replace(b'\n\nside\n', b'\n\nmerge\n')
                latest = git(source, 'hash-object', '-t', 'commit', '-w', '--stdin', data=merge_raw).decode()
                os.chdir(source)
                git(source, 'update-ref', 'refs/heads/master', old)
                mirror_old = mirror.build_mirror(root / 'old-mirror.git', baseline)
                git(source, 'update-ref', 'refs/heads/master', latest)
                expected_mirror = mirror.build_mirror(root / 'new-mirror.git', baseline)
                output = root / 'output'
                options = dict(
                    branch='master', ref=latest, history=3, output=str(output), fetch=False,
                    remote='origin', siteUrl='https://cdn.example/', mirrorUrls=['https://cdn.example/'],
                )
                script = (
                    f'import {{ buildStaticFiles }} from {json.dumps(BUILDER)};\n'
                    'await buildStaticFiles(JSON.parse(process.argv[2]), process.argv[1], process.argv[3]);'
                )
                result = subprocess.run(
                    ['node', '--input-type=module', '-e', script, str(source), json.dumps(options), baseline],
                    capture_output=True,
                )
                self.assertEqual(0, result.returncode, result.stderr.decode('utf-8', errors='replace'))
                manifest = json.loads((output / 'latest.json').read_text(encoding='utf-8'))
                self.assertEqual(latest, manifest['commit'])
                self.assertEqual(expected_mirror, manifest['gitcode_commit'])
                self.assertIn(mirror_old, manifest['gitcode_commits'])
                self.assertEqual(6, len(list(output.glob('*/*.zip'))))
                html = (output / 'index.html').read_text(encoding='utf-8')
                self.assertIn(expected_mirror, html)
                self.assertIn(f'{expected_mirror}/{mirror_old}.zip', html)
                self.assertFalse(list(output.glob('*/pack-*')))

                # 尚未合入基线的分支仍能构建旧版兼容的单 SHA 清单。
                legacy_output = root / 'legacy-output'
                legacy_options = {**options, 'ref': git(source, 'rev-parse', f'{baseline}^').decode(), 'output': str(legacy_output)}
                legacy_result = subprocess.run(
                    ['node', '--input-type=module', '-e', script, str(source), json.dumps(legacy_options), baseline],
                    capture_output=True,
                )
                self.assertEqual(0, legacy_result.returncode, legacy_result.stderr.decode('utf-8', errors='replace'))
                legacy_manifest = json.loads((legacy_output / 'latest.json').read_text(encoding='utf-8'))
                self.assertIsNone(legacy_manifest['gitcode_commit'])
                self.assertEqual([], legacy_manifest['gitcode_commits'])

                # 不完整的浅克隆不能静默生成错误镜像 SHA，允许拉取时应自动补齐。
                shallow = root / 'shallow'
                git(root, 'clone', '--depth=2', '--branch=master', source.as_uri(), str(shallow))
                shallow_output = root / 'shallow-output'
                shallow_options = {**options, 'output': str(shallow_output)}
                shallow_result = subprocess.run(
                    ['node', '--input-type=module', '-e', script, str(shallow), json.dumps(shallow_options), baseline],
                    capture_output=True,
                )
                self.assertNotEqual(0, shallow_result.returncode)
                self.assertFalse(shallow_output.exists())
                shallow_options['fetch'] = True
                fetched_result = subprocess.run(
                    ['node', '--input-type=module', '-e', script, str(shallow), json.dumps(shallow_options), baseline],
                    capture_output=True,
                )
                self.assertEqual(0, fetched_result.returncode, fetched_result.stderr.decode('utf-8', errors='replace'))
                self.assertEqual(expected_mirror, json.loads((shallow_output / 'latest.json').read_text())['gitcode_commit'])

                class StaticSession:
                    def __init__(self):
                        self.paths = []

                    def get(self, url, **kwargs):
                        relative = urlparse(url).path.lstrip('/')
                        self.paths.append(relative)
                        file = output / relative
                        body = file.read_bytes()
                        return SimpleNamespace(status_code=200, text=body.decode() if relative.endswith('.json') else '', content=body)

                for kind, current, target in (('github', old, latest), ('gitcode', mirror_old, expected_mirror)):
                    with self.subTest(kind=kind):
                        local = root / kind
                        # 克隆只含旧版本的仓库，确保更新不是依赖预先存在的新对象。
                        if kind == 'github':
                            git(source, 'update-ref', 'refs/heads/master', old)
                            git(root, 'clone', '--no-local', str(source), str(local))
                            git(source, 'update-ref', 'refs/heads/master', latest)
                        else:
                            git(root, 'clone', '--no-local', str(root / 'old-mirror.git'), str(local))
                            git(local, 'branch', '-M', 'master')
                        # 压缩引用并把远端引用置旧，验证选择的是实际 HEAD。
                        stale = git(local, 'rev-parse', 'HEAD^').decode()
                        git(local, 'update-ref', 'refs/remotes/origin/master', stale)
                        git(local, 'pack-refs', '--all')
                        self.assertNotEqual(current, target)
                        absent = subprocess.run(['git', 'cat-file', '-e', target], cwd=local, capture_output=True)
                        self.assertNotEqual(0, absent.returncode)
                        client = GitOverCdnClient('https://cdn.example', str(local))
                        client.preferred_urls = client.urls
                        client.session = StaticSession()
                        self.assertEqual('behind', client.get_status())
                        self.assertTrue(client.update())
                        self.assertEqual('uptodate', client.get_status())
                        self.assertIn(f'{target}/{current}.zip', client.session.paths)
                        self.assertEqual(target, git(local, 'rev-parse', 'HEAD').decode())
                        self.assertEqual(git(source, 'rev-parse', 'HEAD^{tree}'), git(local, 'rev-parse', 'HEAD^{tree}'))
                        git(local, 'fsck', '--strict')
                        # 已是最新但远端引用过期时，不能 reset 回旧版本。
                        git(local, 'update-ref', 'refs/remotes/origin/master', stale)
                        fresh_client = GitOverCdnClient('https://cdn.example', str(local))
                        fresh_client.preferred_urls = fresh_client.urls
                        fresh_client.session = StaticSession()
                        self.assertTrue(fresh_client.update())
                        self.assertEqual(target, git(local, 'rev-parse', 'HEAD').decode())
                        os.chdir(source)
        finally:
            os.chdir(previous_cwd)

    def test_js_and_python_rewrite_signed_headers_identically(self):
        raw = (
            'tree abc\nparent old\nauthor 测试 <test@example.invalid>\ncommitter C\n'
            'gpgsig signature\n continuation\nmergetag tag\n continuation\nencoding UTF-8\n\n正文\n'
        ).encode('utf-8')
        module = (ROOT / '.github/scripts/mirror_history.mjs').as_uri()
        script = (
            f'import {{ rewriteCommit }} from {json.dumps(module)};\n'
            'process.stdout.write(rewriteCommit(Buffer.from(process.argv[1], "base64"), new Map([["old", "new"]])));'
        )
        result = subprocess.run(
            ['node', '--input-type=module', '-e', script, base64.b64encode(raw).decode()],
            check=True, capture_output=True,
        )
        self.assertEqual(mirror.rewrite_commit(raw, {'old': 'new'}), result.stdout)


if __name__ == '__main__':
    unittest.main()
