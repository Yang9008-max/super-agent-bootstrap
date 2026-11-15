import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
import zipfile
import shutil
import sys

MODULE = Path(__file__).resolve().parents[1] / 'tools' / 'portable_agent.py'
spec = importlib.util.spec_from_file_location('portable_agent', MODULE)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class PortableAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'config.toml').write_text('''model = "my-model"
model_provider = "relay"
model_reasoning_effort = "high"
notify = ["C:/Windows/python.exe"]
[model_providers.relay]
name = "Personal provider"
base_url = "https://example.test/v1"
wire_api = "responses"
requires_openai_auth = true
http_headers = { Authorization = "secret-value" }
[projects."C:/work"]
trust_level = "trusted"
''', encoding='utf-8')
        (self.source / 'auth.json').write_text('{"OPENAI_API_KEY":"secret-value"}')
        self.skills = self.source / 'skills'
        skill = self.skills / 'example'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: example\n---\nA personal skill.\n')
        (skill / '.env').write_text('SECRET=secret-value')
        (skill / 'run.sh').write_text('#!/bin/sh\nprintf hello\n')
        self.profile = agent.export_profile(self.source, [self.skills])
        self.home = self.root / 'target'
        self.home.mkdir()

    def apply(self, profile=None):
        return agent.apply_profile(profile or self.profile, self.home, self.home / '.codex')

    def test_export_only_portable_config_and_no_credentials(self):
        serialized = json.dumps(self.profile)
        self.assertNotIn('secret-value', serialized)
        self.assertNotIn('C:/Windows', serialized)
        self.assertNotIn('projects', self.profile['config'])
        provider = self.profile['config']['model_providers']['relay']
        self.assertEqual(provider['base_url'], 'https://example.test/v1')
        self.assertEqual(provider['env_key'], 'OPENAI_API_KEY')
        self.assertFalse(provider['requires_openai_auth'])
        self.assertEqual(self.profile['required_env'], ['OPENAI_API_KEY'])
        self.assertEqual(len(self.profile['skills']), 1)
        self.assertNotIn('.env', self.profile['skills']['example'])

    def test_restore_merge_backup_and_idempotence(self):
        codex = self.home / '.codex'
        codex.mkdir()
        config = codex / 'config.toml'
        original = 'model = "old"\n[projects."/work"]\ntrust_level = "trusted"\n'
        config.write_text(original)
        result = self.apply()
        parsed = tomllib.loads(config.read_text(encoding='utf-8'))
        self.assertEqual(parsed['model'], 'my-model')
        self.assertEqual(parsed['projects']['/work']['trust_level'], 'trusted')
        self.assertEqual((Path(result['backup']) / 'codex' / 'config.toml').read_text(), original)
        self.assertTrue((self.home / '.agents/skills/example/SKILL.md').is_file())
        self.assertEqual(self.apply()['changed'], 0)
        self.assertIsNone(self.apply()['backup'])

    def test_reject_traversal_before_config_changes(self):
        self.profile['skills']['example']['../../escape'] = {
            'content': base64.b64encode(b'bad').decode(), 'executable': False}
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse((self.home / '.codex/config.toml').exists())
        self.assertFalse((self.home / 'escape').exists())

    def test_reject_existing_symlink_parent(self):
        outside = self.root / 'outside'
        outside.mkdir()
        try:
            (self.home / '.agents').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not allow symlinks')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(list(outside.iterdir()), [])

    def test_reject_credentials_in_url(self):
        self.profile['config']['model_providers']['relay']['base_url'] = 'https://user:password@example.test/v1'
        with self.assertRaises(ValueError):
            self.apply()

    def test_reject_token_query_in_url(self):
        self.profile['config']['model_providers']['relay']['base_url'] = 'https://example.test/v1?api_key=secret'
        with self.assertRaises(ValueError):
            self.apply()

    def test_reject_corrupt_base64_before_writes(self):
        self.profile['skills']['example']['SKILL.md']['content'] = 'not base64!'
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse((self.home / '.codex/config.toml').exists())

    def test_toml_serializer_round_trip(self):
        doc = {'model': '含引号"换行\n', 'model_providers': {'a.b': {'env_key': 'KEY'}},
               'mcp_servers': {'server': {'args': ['a', 'b'], 'env': {'A': 'B'}}},
               'flag': True, 'n': 4, 'f': 0.5}
        self.assertEqual(tomllib.loads(agent.toml_dump(doc)), doc)

    def test_bundle_embeds_tool_profile_and_checksums(self):
        target = self.root / 'personal.sh'
        agent.bundle(self.profile, target)
        script = target.read_text(encoding='utf-8')
        payload = script.split('\n__SUPER_AGENT_PAYLOAD__\n', 1)[1]
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload))) as z:
            self.assertEqual(json.loads(z.read('profile.json'))['config']['model'], 'my-model')
            self.assertIn('portable_agent.py', z.namelist())
        self.assertNotIn(str(self.source), script)
        self.assertIn('PAYLOAD_SHA256=', script)

    def test_export_refuses_overwrite(self):
        output = self.root / 'profile.json'
        args = [str(MODULE), 'export', '--codex-home', str(self.source), '--output', str(output)]
        import sys
        first = subprocess.run([sys.executable, *args], capture_output=True, text=True)
        second = subprocess.run([sys.executable, *args], capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotEqual(second.returncode, 0)
        self.assertNotIn('secret-value', first.stdout + first.stderr + second.stderr)

    def test_doctor_reports_missing_env_without_values(self):
        self.apply()
        result = agent.doctor(self.home / '.codex', env={})
        self.assertEqual(result['missing_env'], ['OPENAI_API_KEY'])
        self.assertNotIn('https://', json.dumps(result))

    def test_failed_write_rolls_back_prior_changes(self):
        from unittest.mock import patch
        codex = self.home / '.codex'
        codex.mkdir()
        config = codex / 'config.toml'
        config.write_text('model = "old"\n')
        original_write = agent.atomic_write
        def fail_skill(path, content, executable=False):
            if path.name == 'SKILL.md':
                raise OSError('simulated disk failure')
            return original_write(path, content, executable)
        with patch.object(agent, 'atomic_write', side_effect=fail_skill):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(config.read_text(), 'model = "old"\n')


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.bash = os.environ.get('PORTABLE_AGENT_TEST_BASH') or (shutil.which('bash') if os.name != 'nt' else None)
        if not self.bash:
            self.skipTest('Set PORTABLE_AGENT_TEST_BASH to enable Bash integration tests')
        self.temp = tempfile.TemporaryDirectory(suffix=' agent tests')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.stubs = self.root / 'stubs'
        self.stubs.mkdir()
        self.stub('uname', 'if [[ "$1" == -s ]]; then printf "Linux\\n"; else printf "x86_64\\n"; fi\n')
        interpreter = sys.executable.replace('\\', '/')
        self.stub('python3', 'exec "' + interpreter + '" "$@"\n')
        self.profile = {'version': 1, 'config': {'model': 'bootstrap-test'}, 'skills': {},
                        'instructions': None, 'required_env': [], 'warnings': []}
        self.bundle = self.root / 'personal.sh'
        agent.bundle(self.profile, self.bundle)
        self.env = dict(os.environ)
        self.env.pop('CODEX_HOME', None)
        self.env['USERPROFILE'] = str(self.home)
        self.env['HOME'] = self.home.as_posix()

    def stub(self, name, body):
        target = self.stubs / name
        target.write_text('#!/usr/bin/env bash\n' + body, encoding='utf-8', newline='\n')
        target.chmod(0o700)

    def run_bundle(self, *args):
        # Convert test paths inside Bash instead of mixing host PATH conventions.
        command = 'export HOME="$(cd "$1" && pwd)"; export PATH="$(cd "$2" && pwd):$PATH"; shift 2; bash "$@"'
        return subprocess.run([self.bash, '-c', command, 'bootstrap-test', self.home.as_posix(),
                               self.stubs.as_posix(), self.bundle.as_posix(), *args],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_bundle_restore_and_installed_launcher(self):
        first = self.run_bundle('--skip-codex')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(tomllib.loads((self.home / '.codex/config.toml').read_text())['model'], 'bootstrap-test')
        self.assertTrue((self.home / '.local/bin/super-agent').is_file())
        second = self.run_bundle('--skip-codex')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn('"changed": 0', second.stdout)

    def test_uv_fallback_uses_managed_python_without_system_changes(self):
        self.stub('python3', 'exit 1\n')
        interpreter = sys.executable.replace('\\', '/')
        self.stub('uv', '[[ "$1" == run ]] || exit 8\nshift 6\nexec "' + interpreter + '" "$@"\n')
        result = self.run_bundle('--skip-codex')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.home / '.codex/config.toml').is_file())

    def test_tampered_bundle_fails_before_configuration(self):
        text = self.bundle.read_text()
        self.bundle.write_text(text.replace('PAYLOAD_SHA256="', 'PAYLOAD_SHA256="0', 1), newline='\n')
        result = self.run_bundle('--skip-codex')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum mismatch', result.stderr)
        self.assertFalse((self.home / '.codex/config.toml').exists())

    def test_check_only_does_not_apply_configuration(self):
        result = self.run_bundle('--check-only')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.home / '.codex/config.toml').exists())


class GitHubBootstrapTests(unittest.TestCase):
    stub = BootstrapTests.stub

    def setUp(self):
        BootstrapTests.setUp(self)
        self.assets = self.root / 'release assets'
        self.assets.mkdir()
        shutil.copyfile(self.bundle, self.assets / 'super-agent-personal.sh')
        import hashlib
        digest = hashlib.sha256(self.bundle.read_bytes()).hexdigest()
        (self.assets / 'super-agent-personal.sh.sha256').write_text(digest + '  super-agent-personal.sh\n')
        self.env['PORTABLE_AGENT_TEST_ASSETS'] = self.assets.as_posix()
        self.stub('gh', '''if [[ "$1" == auth ]]; then exit 0; fi
[[ "$1" == release && "$2" == download ]] || exit 8
destination=''
while (($#)); do
  if [[ "$1" == --dir ]]; then destination="$2"; shift 2; else shift; fi
done
cp "$PORTABLE_AGENT_TEST_ASSETS/super-agent-personal.sh" "$destination/"
cp "$PORTABLE_AGENT_TEST_ASSETS/super-agent-personal.sh.sha256" "$destination/"
''')
        self.launcher = MODULE.parents[1] / 'deploy/github-install.sh'

    def run_launcher(self):
        command = 'export HOME="$(cd "$1" && pwd)"; export PATH="$(cd "$2" && pwd):$PATH"; shift 2; bash "$@"'
        return subprocess.run([self.bash, '-c', command, 'github-bootstrap-test', self.home.as_posix(),
                               self.stubs.as_posix(), self.launcher.as_posix(), '--skip-codex'],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_private_release_download_and_restore(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.home / '.codex/config.toml').is_file())
        self.assertIn('[4/4]', result.stdout)

    def test_corrupt_release_checksum_rejected(self):
        with (self.assets / 'super-agent-personal.sh').open('a') as stream:
            stream.write('\n# modified after publishing\n')
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum mismatch', result.stderr)
        self.assertFalse((self.home / '.codex/config.toml').exists())


class ServerBootstrapTests(unittest.TestCase):
    stub = BootstrapTests.stub

    def setUp(self):
        BootstrapTests.setUp(self)
        self.assets = self.root / 'server assets'
        self.assets.mkdir()
        shutil.copyfile(self.bundle, self.assets / 'super-agent-personal.sh')
        import hashlib
        digest = hashlib.sha256(self.bundle.read_bytes()).hexdigest()
        (self.assets / 'super-agent-personal.sh.sha256').write_text(digest + '  super-agent-personal.sh\n')
        self.env['PORTABLE_AGENT_TEST_ASSETS'] = self.assets.as_posix()
        self.stub('curl', '''url=''
destination=''
while (($#)); do
  case "$1" in
    -o) destination="$2"; shift 2 ;;
    https://*) url="$1"; shift ;;
    *) shift ;;
  esac
done
cp "$PORTABLE_AGENT_TEST_ASSETS/${url##*/}" "$destination"
''')
        self.launcher = self.root / 'server-install.sh'
        template = (MODULE.parents[1] / 'deploy/portable-agent-server-install.sh').read_text(encoding='utf-8')
        self.launcher.write_text(template.replace('__BUNDLE_BASE_URL__', 'https://example.test/private-download'), encoding='utf-8', newline='\n')

    def run_launcher(self):
        command = 'export HOME="$(cd "$1" && pwd)"; export PATH="$(cd "$2" && pwd):$PATH"; shift 2; bash "$@"'
        return subprocess.run([self.bash, '-c', command, 'server-bootstrap-test', self.home.as_posix(),
                               self.stubs.as_posix(), self.launcher.as_posix(), '--skip-codex'],
                              env=self.env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)

    def test_server_download_without_github(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.home / '.codex/config.toml').is_file())

    def test_server_checksum_failure_does_not_apply(self):
        with (self.assets / 'super-agent-personal.sh').open('a') as stream:
            stream.write('\n# corrupted transfer\n')
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum mismatch', result.stderr)
        self.assertFalse((self.home / '.codex/config.toml').exists())


if __name__ == '__main__':
    unittest.main()
