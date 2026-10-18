import copy
import hashlib
from pathlib import Path
import plistlib
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import kilo_zh as patcher


class PatchTests(unittest.TestCase):
    def test_multibyte_offsets_are_bytes_and_application_is_exact(self):
        original = '前缀 `Settings` 尾部'.encode()
        before = b'`Settings`'
        after = '`设置`'.encode()
        asset = {
            'before_sha256': patcher.sha256(original),
            'after_sha256': patcher.sha256(original.replace(before, after)),
            'edits': [{'offset': original.index(before), 'length': len(before),
                       'before_sha256': patcher.sha256(before), 'replacement': '`设置`'}],
        }
        self.assertEqual(patcher.apply_edits(original, asset), original.replace(before, after))
        with self.assertRaises(patcher.PatchError):
            patcher.apply_edits(original + b' ', asset)

    def test_overlapping_or_tampered_records_are_rejected(self):
        source = b'`Settings`'
        edit = {'offset': 0, 'length': len(source), 'before_sha256': patcher.sha256(source),
                'replacement': '`设置`'}
        asset = {'before_sha256': patcher.sha256(source),
                 'after_sha256': patcher.sha256('`设置`'.encode()), 'edits': [edit, edit]}
        with self.assertRaises(patcher.PatchError):
            patcher.apply_edits(source, asset)
        asset['edits'] = [dict(edit, before_sha256='0' * 64)]
        with self.assertRaises(patcher.PatchError):
            patcher.apply_edits(source, asset)

    def test_template_expression_is_retained(self):
        source = b'`Copy ${name.toLowerCase()}`'
        replacement = '`复制${name.toLowerCase()}`'
        asset = {'before_sha256': patcher.sha256(source),
                 'after_sha256': patcher.sha256(replacement.encode()),
                 'edits': [{'offset': 0, 'length': len(source),
                            'before_sha256': patcher.sha256(source), 'replacement': replacement}]}
        self.assertIn(b'${name.toLowerCase()}', patcher.apply_edits(source, asset))

    def test_asar_repacking_keeps_unmodified_data_and_updates_offsets(self):
        old, new, untouched = b'`Settings`', '`设置页面`'.encode(), b'unchanged-business-code'
        def metadata(data, offset):
            return {'size': len(data), 'offset': str(offset), 'integrity': {
                'algorithm': 'SHA256', 'hash': patcher.sha256(data), 'blockSize': 4,
                'blocks': [patcher.sha256(data[i:i+4]) for i in range(0, len(data), 4)]}}
        header = {'files': {'ui.js': metadata(old, 0), 'backend.js': metadata(untouched, len(old)),
                            'native.node': {'size': 10, 'unpacked': True}, 'alias': {'link': 'ui.js'}}}
        prefix, _ = patcher.make_header(header)
        original = prefix + old + untouched
        expected_header = copy.deepcopy(header)
        expected_header['files']['ui.js'] = metadata(new, 0)
        expected_header['files']['backend.js']['offset'] = str(len(new))
        expected_prefix, expected_raw = patcher.make_header(expected_header)
        expected = expected_prefix + new + untouched
        plan = {'original_asar_sha256': patcher.sha256(original),
                'patched_asar_sha256': patcher.sha256(expected),
                'assets': {'ui.js': {'before_sha256': patcher.sha256(old),
                                    'after_sha256': patcher.sha256(new), 'edits': [{
                    'offset': 0, 'length': len(old), 'before_sha256': patcher.sha256(old),
                    'replacement': new.decode()}]}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.asar'
            path.write_bytes(original)
            self.assertEqual(patcher.patch_asar(path, plan), patcher.sha256(expected_raw))
            self.assertEqual(path.read_bytes(), expected)

    def test_bad_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'app.asar'
            path.write_bytes(struct.pack('<4I', 4, 12, 8, 400))
            with self.assertRaises(patcher.PatchError):
                patcher.read_asar(path)

    def test_embedded_digest_stays_enabled_and_fails_closed(self):
        old, new = b'a' * 32, b'b' * 32
        data = b'prefix' + patcher.SENTINEL + b'\x01\x01' + old + b'suffix'
        result = patcher.replace_embedded_digest(data, old, new)
        self.assertEqual(result, b'prefix' + patcher.SENTINEL + b'\x01\x01' + new + b'suffix')
        for invalid in [data + patcher.SENTINEL, data.replace(b'\x01\x01', b'\x00\x01')]:
            with self.assertRaises(patcher.PatchError):
                patcher.replace_embedded_digest(invalid, old, new)
        with self.assertRaises(patcher.PatchError):
            patcher.replace_embedded_digest(data, b'c' * 32, new)

    def test_dictionary_digest_matches_electron_concatenation(self):
        data = {'ElectronAsarIntegrity': {'Resources/app.asar': {'algorithm': 'SHA256', 'hash': 'abc'}}}
        self.assertEqual(patcher.dictionary_digest(data), hashlib.sha256(b'Resources/app.asarSHA256abc').digest())

    def test_entitlements_do_not_silently_drop_unrecognized_values(self):
        raw = b'[Dict]\n [Key] allow-jit\n [Value]\n [Bool] true\n'
        self.assertEqual(patcher.parse_entitlements(raw), {'allow-jit': True})
        with self.assertRaises(patcher.PatchError):
            patcher.parse_entitlements(raw + b'[Key] unknown\n[Value]\n[String] value\n')
        self.assertEqual(patcher.parse_entitlements(plistlib.dumps({'allow-jit': True})), {'allow-jit': True})

    def test_manifest_contains_only_relative_assets_and_expected_count(self):
        patch = patcher.load_patch()
        self.assertEqual(patch['translation_count'], 968)
        self.assertEqual(len(patch['assets']), 3)
        for name in patch['assets']:
            self.assertFalse(Path(name).is_absolute())
            self.assertNotIn('..', Path(name).parts)

    def test_build_requires_acknowledgement_before_backup_or_mutation(self):
        args = SimpleNamespace(allow_local_library_signing=False)
        with patch.object(patcher.sys, 'platform', 'darwin'), \
             patch.object(patcher.platform, 'machine', return_value='arm64'), \
             patch.object(patcher, 'create_backup') as backup:
            with self.assertRaises(patcher.PatchError):
                patcher.build(args, patcher.load_patch())
            backup.assert_not_called()

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'Kilo.app'
            output.mkdir()
            marker = output / 'keep.txt'
            marker.write_text('existing application')
            args = SimpleNamespace(allow_local_library_signing=True,
                                   app=Path(directory) / 'original.app', output=output,
                                   backup_dir=Path(directory) / 'backups')
            with patch.object(patcher.sys, 'platform', 'darwin'), \
                 patch.object(patcher.platform, 'machine', return_value='arm64'), \
                 patch.object(patcher, 'create_backup') as backup:
                with self.assertRaises(patcher.PatchError):
                    patcher.build(args, patcher.load_patch())
                backup.assert_not_called()
            self.assertEqual(marker.read_text(), 'existing application')


if __name__ == '__main__':
    unittest.main()
