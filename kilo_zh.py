#!/usr/bin/env python3
"""Version-pinned, offline Chinese UI patch builder for Kilo Desktop on macOS."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent
PATCH_FILE = ROOT / 'patches/kilo-0.1.12-darwin-arm64.json'
ASAR_PATH = Path('Contents/Resources/app.asar')
PLIST_PATH = Path('Contents/Info.plist')
FRAMEWORK_PATH = Path('Contents/Frameworks/Electron Framework.framework')
FRAMEWORK_BINARY = FRAMEWORK_PATH / 'Versions/A/Electron Framework'
SENTINEL = b'AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A'
LIBRARY_ENTITLEMENT = 'com.apple.security.cs.disable-library-validation'


class PatchError(Exception):
    """A validation failed; do not continue with a potentially different build."""


def require(condition, message):
    if not condition:
        raise PatchError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(*args):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True)


def load_patch():
    patch = json.loads(PATCH_FILE.read_text(encoding='utf-8'))
    require(patch.get('format_version') == 1, '不支持的补丁格式。')
    count = sum(len(asset['edits']) for asset in patch['assets'].values())
    require(count == patch['translation_count'], '补丁记录数量不一致。')
    return patch


def read_asar(path):
    with Path(path).open('rb') as source:
        prefix = source.read(16)
        require(len(prefix) == 16, 'ASAR 头部不完整。')
        marker, pickle_size, payload_size, json_size = struct.unpack('<4I', prefix)
        require(marker == 4 and pickle_size == payload_size + 4, '不支持的 ASAR 头部。')
        require(0 < json_size <= payload_size - 4 <= 64 * 1024 * 1024,
                'ASAR 头部大小无效。')
        raw = source.read(json_size)
        require(len(raw) == json_size, 'ASAR 头部被截断。')
    return json.loads(raw), 8 + pickle_size, raw


def entries(header, prefix=''):
    for name, meta in header['files'].items():
        path = prefix + name
        if 'files' in meta:
            yield from entries(meta, path + '/')
        else:
            yield path, meta


def packed_entries(header):
    return sorted(((path, meta) for path, meta in entries(header)
                   if 'offset' in meta and not meta.get('unpacked')),
                  key=lambda pair: int(pair[1]['offset']))


def apply_edits(source, asset):
    require(sha256(source) == asset['before_sha256'], '界面文件与支持的原版不一致。')
    edits = sorted(asset['edits'], key=lambda edit: edit['offset'])
    previous_end = 0
    for edit in edits:
        offset, length = edit['offset'], edit['length']
        require(isinstance(offset, int) and isinstance(length, int)
                and length > 0 and previous_end <= offset <= len(source) - length,
                '补丁位置越界或重叠。')
        require(sha256(source[offset:offset + length]) == edit['before_sha256'],
                '待替换的文字校验失败。')
        previous_end = offset + length
    result = source
    for edit in reversed(edits):
        start = edit['offset']
        result = (result[:start] + edit['replacement'].encode('utf-8')
                  + result[start + edit['length']:])
    require(sha256(result) == asset['after_sha256'], '汉化后的界面文件校验失败。')
    return result


def make_header(header):
    raw = json.dumps(header, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    padding = (-(4 + len(raw))) % 4
    pickle = struct.pack('<II', 4 + len(raw) + padding, len(raw)) + raw + b'\0' * padding
    return struct.pack('<II', 4, len(pickle)) + pickle, raw


def patch_asar(path, patch):
    path = Path(path)
    require(file_sha256(path) == patch['original_asar_sha256'], '原版资源包 SHA-256 不匹配。')
    header, data_start, _ = read_asar(path)
    all_files = dict(entries(header))
    require(set(patch['assets']) <= set(all_files), '资源包中缺少目标界面文件。')
    replacements = {}
    with path.open('rb') as source:
        for name, asset in patch['assets'].items():
            meta = all_files[name]
            require('offset' in meta and not meta.get('unpacked'), '目标文件未打包。')
            source.seek(data_start + int(meta['offset']))
            replacements[name] = apply_edits(source.read(meta['size']), asset)

    layout = []
    offset = 0
    for name, meta in packed_entries(header):
        layout.append((name, int(meta['offset']), meta['size']))
        if name in replacements:
            data = replacements[name]
            block_size = meta['integrity']['blockSize']
            require(block_size > 0, '无效的文件校验块大小。')
            meta['size'] = len(data)
            meta['integrity'] = {
                'algorithm': 'SHA256', 'hash': sha256(data), 'blockSize': block_size,
                'blocks': [sha256(data[i:i + block_size])
                           for i in range(0, len(data), block_size)],
            }
        meta['offset'] = str(offset)
        offset += meta['size']

    prefix, raw_header = make_header(header)
    temp = path.with_name(path.name + '.zh-tmp')
    try:
        with path.open('rb') as source, temp.open('wb') as dest:
            dest.write(prefix)
            for name, old_offset, old_size in layout:
                if name in replacements:
                    dest.write(replacements[name])
                else:
                    source.seek(data_start + old_offset)
                    remaining = old_size
                    while remaining:
                        block = source.read(min(remaining, 4 * 1024 * 1024))
                        require(block, '原版资源包被截断。')
                        dest.write(block)
                        remaining -= len(block)
        require(file_sha256(temp) == patch['patched_asar_sha256'],
                '生成的完整资源包校验失败。')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return sha256(raw_header)


def dictionary_digest(plist):
    h = hashlib.sha256()
    for path, info in sorted(plist['ElectronAsarIntegrity'].items()):
        for value in (path, info['algorithm'], info['hash']):
            h.update(value.encode('utf-8'))
    return h.digest()


def replace_embedded_digest(data, expected, replacement):
    require(data.count(SENTINEL) == 1, '内嵌校验摘要位置不唯一；停止处理。')
    offset = data.index(SENTINEL) + len(SENTINEL)
    require(data[offset:offset + 2] == b'\x01\x01', '完整性校验未开启或格式不受支持。')
    require(data[offset + 2:offset + 34] == expected, '原始内嵌摘要与 Info.plist 不一致。')
    require(len(replacement) == 32, '摘要长度无效。')
    return data[:offset + 2] + replacement + data[offset + 34:]


def update_integrity(app, header_hash):
    path = app / PLIST_PATH
    original_bytes = path.read_bytes()
    plist = plistlib.loads(original_bytes)
    expected = dictionary_digest(plist)
    plist['ElectronAsarIntegrity']['Resources/app.asar']['hash'] = header_hash
    binary = app / FRAMEWORK_BINARY
    binary.write_bytes(replace_embedded_digest(binary.read_bytes(), expected,
                                             dictionary_digest(plist)))
    fmt = plistlib.FMT_BINARY if original_bytes.startswith(b'bplist') else plistlib.FMT_XML
    path.write_bytes(plistlib.dumps(plist, fmt=fmt, sort_keys=False))


def parse_entitlements(data):
    if data.lstrip().startswith((b'<?xml', b'<plist', b'bplist')):
        result = plistlib.loads(data)
        require(isinstance(result, dict), '无效的签名权限。')
        return result
    # Recent codesign versions print DER entitlements in a typed, human-readable form.
    text = data.decode('utf-8')
    pairs = re.findall(r'\[Key\] ([^\n]+)\n\s*\[Value\]\n\s*\[Bool\] (true|false)', text)
    require(pairs and len(pairs) == text.count('[Key]'),
            '无法完整读取原签名权限；不会猜测或丢弃权限。')
    return {key: value == 'true' for key, value in pairs}


def sign_app(app, workdir):
    bundles = sorted((app / 'Contents/Frameworks').glob('*.app')) + [app]
    entitlements = []
    for index, bundle in enumerate(bundles):
        original = parse_entitlements(run('/usr/bin/codesign', '-d', '--entitlements', '-', bundle).stdout)
        updated = dict(original)
        updated[LIBRARY_ENTITLEMENT] = True
        path = workdir / ('entitlements-' + str(index) + '.plist')
        path.write_bytes(plistlib.dumps(updated))
        entitlements.append((bundle, path, updated))
    run('/usr/bin/codesign', '--force', '--deep', '--sign', '-',
        '--preserve-metadata=identifier,entitlements,flags,runtime', '--timestamp=none', app)
    for bundle, path, expected in entitlements:
        run('/usr/bin/codesign', '--force', '--sign', '-',
            '--preserve-metadata=identifier,flags,runtime', '--entitlements', path,
            '--timestamp=none', bundle)
        actual = parse_entitlements(run('/usr/bin/codesign', '-d', '--entitlements', '-', bundle).stdout)
        require(actual == expected, '签名后的权限与预期不一致。')
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)


def inspect_app(app, patch):
    require(app.is_dir() and app.suffix == '.app', '请提供存在的 Kilo.app 路径。')
    plist = plistlib.loads((app / PLIST_PATH).read_bytes())
    require(plist.get('CFBundleIdentifier') == patch['bundle_id'], '此应用不是 Kilo Desktop。')
    require(plist.get('CFBundleShortVersionString') == patch['app_version'],
            '目前仅支持 Kilo Desktop ' + patch['app_version'] + '。')
    framework_info = plistlib.loads((app / FRAMEWORK_PATH / 'Resources/Info.plist').read_bytes())
    require(framework_info.get('CFBundleVersion') == patch['electron_version'],
            '内置 Electron 版本不匹配。')
    binary = (app / 'Contents/MacOS/Kilo').read_bytes()[:8]
    require(len(binary) == 8 and struct.unpack('<II', binary) == (0xFEEDFACF, 0x0100000C),
            '目前仅支持 macOS Apple Silicon（arm64）原版构建。')
    actual = file_sha256(app / ASAR_PATH)
    if actual == patch['original_asar_sha256']:
        return 'original'
    if actual == patch['patched_asar_sha256']:
        return 'patched'
    raise PatchError('资源包与本补丁不匹配。即使版本号相同，也不能强行应用。')


def verify_app(app, patch, check_signature=True):
    require(inspect_app(app, patch) == 'patched', '此应用尚未应用本版本汉化。')
    header, start, raw = read_asar(app / ASAR_PATH)
    plist = plistlib.loads((app / PLIST_PATH).read_bytes())
    require(plist['ElectronAsarIntegrity']['Resources/app.asar']['hash'] == sha256(raw),
            '资源头部摘要不一致。')
    data = (app / FRAMEWORK_BINARY).read_bytes()
    # Validation only: replacing a matching digest with itself must not change the binary.
    require(replace_embedded_digest(data, dictionary_digest(plist), dictionary_digest(plist)) == data,
            '内嵌摘要不一致。')
    with (app / ASAR_PATH).open('rb') as source:
        for name, meta in packed_entries(header):
            source.seek(start + int(meta['offset']))
            content = source.read(meta['size'])
            require(len(content) == meta['size'], '文件被截断：' + name)
            integrity = meta.get('integrity')
            if integrity:
                require(integrity['hash'] == sha256(content), '文件校验失败：' + name)
                size = integrity['blockSize']
                require(size > 0, '无效的文件校验块大小：' + name)
                expected = [sha256(content[i:i + size]) for i in range(0, len(content), size)]
                # Empty files in upstream archives may include one empty-block digest.
                if not content and integrity['blocks'] == [sha256(b'')]:
                    expected = [sha256(b'')]
                require(expected == integrity['blocks'], '文件块校验失败：' + name)
    if check_signature:
        run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)


def create_backup(app, directory, patch):
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    backup = directory / ('Kilo-' + patch['app_version'] + '-original-' + stamp + '.zip')
    run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, backup)
    with zipfile.ZipFile(backup) as archive:
        require(archive.testzip() is None, '备份 ZIP 校验失败。')
        h = hashlib.sha256()
        with archive.open(app.name + '/' + ASAR_PATH.as_posix()) as source:
            for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
                h.update(block)
        require(h.hexdigest() == patch['original_asar_sha256'], '备份中的原版资源包发生变化。')
    checksum = backup.with_suffix('.zip.sha256')
    checksum.write_text(file_sha256(backup) + '  ' + backup.name + '\n', encoding='utf-8')
    return backup


def build(args, patch):
    require(sys.platform == 'darwin' and platform.machine() == 'arm64',
            '构建及本地签名需要 Apple Silicon Mac。')
    require(args.allow_local_library_signing,
            '本地签名需要放宽 Kilo 自身的动态库校验。理解风险后，显式添加 --allow-local-library-signing。')
    app, output = args.app.expanduser().resolve(), args.output.expanduser().resolve()
    backup_dir = args.backup_dir.expanduser().resolve()
    require(output.name == 'Kilo.app', '输出名称必须为 Kilo.app。')
    require(not output.exists(), '输出路径已存在；不会覆盖已有应用。')
    require(app != output and app not in output.parents and output not in app.parents,
            '输入与输出必须是相互独立的路径。')
    require(app != backup_dir and app not in backup_dir.parents, '备份目录不能位于原应用内。')
    require(inspect_app(app, patch) == 'original', '该应用已汉化，请使用原版备份作为输入。')
    print('校验通过，正在创建并验证完整原版备份…', flush=True)
    backup = create_backup(app, backup_dir, patch)
    print('原版备份：' + str(backup), flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.kilo-zh-', dir=output.parent) as directory:
        workdir = Path(directory)
        staged = workdir / 'Kilo.app'
        run('/usr/bin/ditto', app, staged)
        require(inspect_app(staged, patch) == 'original', '复制期间源应用发生变化。')
        header_hash = patch_asar(staged / ASAR_PATH, patch)
        update_integrity(staged, header_hash)
        print('已应用中文补丁，正在本地签名并校验…', flush=True)
        sign_app(staged, workdir)
        verify_app(staged, patch)
        require(not output.exists(), '输出路径已被其他进程创建；停止覆盖。')
        os.rename(staged, output)
    print('构建完成：' + str(output))
    print('请退出 Kilo，用访达将此 Kilo.app 拖入“应用程序”并替换，再启动验证。')
    print('本工具未修改原应用、用户配置或系统安全设置。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['inspect', 'verify', 'build']:
        command = commands.add_parser(name)
        command.add_argument('--app', type=Path, default=Path('/Applications/Kilo.app'))
        if name == 'build':
            command.add_argument('--output', type=Path, default=ROOT / 'build/Kilo.app')
            command.add_argument('--backup-dir', type=Path, default=ROOT / 'backups')
            command.add_argument('--allow-local-library-signing', action='store_true')
    args = parser.parse_args(argv)
    try:
        patch = load_patch()
        if args.command == 'inspect':
            state = inspect_app(args.app.expanduser().resolve(), patch)
            print('支持的原版，可构建中文副本。' if state == 'original' else '已安装此版本中文补丁。')
        elif args.command == 'verify':
            require(sys.platform == 'darwin', '签名校验需要 macOS。')
            verify_app(args.app.expanduser().resolve(), patch)
            print('通过：资源文件、内嵌完整性摘要及本地签名。请另行验证界面与服务连接。')
        else:
            build(args, patch)
        return 0
    except (PatchError, OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print('停止：' + str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.decode('utf-8', errors='replace').strip(), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
