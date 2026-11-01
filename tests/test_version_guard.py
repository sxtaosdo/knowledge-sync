import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('knowledge_sync_version_guard',ROOT/'scripts/git_version.py')
guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
GIT='C:/Program Files/Git/cmd/git.exe'


class VersionGuardLogicTests(unittest.TestCase):
    def test_counter_spans_prefixes_and_ignores_nonversion_subjects(self):
        self.assertEqual(guard.next_version('0.1.0.0',[]),'0.1.0.1')
        self.assertEqual(guard.next_version('0.2.0.0',['v0.1.0.13: old','fixture','v0.0.9.10: older']),'0.2.0.14')

    def test_permission_failure_is_not_empty_initial_history(self):
        with patch.object(guard,'run_git',side_effect=subprocess.CalledProcessError(128,['git','log'],stderr='Permission denied')):
            with self.assertRaises(subprocess.CalledProcessError):guard.subjects()


class VersionGuardHookTests(unittest.TestCase):
    def test_unborn_index_bump_and_message_guard_without_launching_shell(self):
        with tempfile.TemporaryDirectory(dir='C:/Users/33272/AppData/Local/Temp') as temporary:
            repo=Path(temporary);(repo/'scripts').mkdir()
            shutil.copyfile(ROOT/'scripts/git_version.py',repo/'scripts/git_version.py')
            def git(*args):
                return subprocess.run([GIT,*args],cwd=repo,capture_output=True,text=True,encoding='utf-8')
            def script(*args):
                return subprocess.run([sys.executable,'-X','utf8','-B','scripts/git_version.py',*args],cwd=repo,
                                      capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(git('init','-b','main').returncode,0)
            (repo/'VERSION').write_text('0.1.0.0\n',encoding='utf-8')
            (repo/'README.md').write_text('# Synthetic\n\n**当前版本：`v0.1.0.0`**\n',encoding='utf-8')
            self.assertEqual(git('add','README.md','VERSION').returncode,0)
            bumped=script('bump');self.assertEqual(bumped.returncode,0,bumped.stderr)
            self.assertEqual((repo/'VERSION').read_text().strip(),'0.1.0.1')
            self.assertEqual(script('check-staged').returncode,0)
            message=repo/'synthetic-message.txt';message.write_text('v0.1.0.1: fixture\n\nMissing Agent\n')
            self.assertNotEqual(script('check-commit-msg',str(message)).returncode,0)
            message.write_text('v0.1.0.1: fixture\n\nAgent: codex\n')
            self.assertEqual(script('check-commit-msg',str(message)).returncode,0)
            self.assertEqual(git('rev-parse','--verify','--quiet','HEAD').returncode,1)

    def test_initial_and_subsequent_hooks_enforce_version_and_agent(self):
        with tempfile.TemporaryDirectory(dir='C:/Users/33272/AppData/Local/Temp') as temporary:
            repo=Path(temporary);(repo/'scripts').mkdir();(repo/'.githooks').mkdir()
            shutil.copyfile(ROOT/'scripts/git_version.py',repo/'scripts/git_version.py')
            for name in ('pre-commit','commit-msg'):
                shutil.copyfile(ROOT/'.githooks'/name,repo/'.githooks'/name)
            env=dict(os.environ,PATH=str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'],PYTHONUTF8='1')
            def git(*args):
                return subprocess.run([GIT,*args],cwd=repo,env=env,capture_output=True,text=True,encoding='utf-8')
            def script(*args):
                return subprocess.run([sys.executable,'-X','utf8','-B','scripts/git_version.py',*args],cwd=repo,
                                      env=env,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(git('init','-b','main').returncode,0)
            self.assertEqual(git('config','user.name','Synthetic version fixture').returncode,0)
            self.assertEqual(git('config','user.email','fixture@example.invalid').returncode,0)
            self.assertEqual(script('install').returncode,0)
            (repo/'VERSION').write_text('0.1.0.1\n',encoding='utf-8')
            (repo/'README.md').write_text('# Synthetic\n\n**当前版本：`v0.1.0.1`**\n',encoding='utf-8')
            self.assertEqual(git('add','VERSION','README.md','scripts/git_version.py','.githooks').returncode,0)
            rejected=git('commit','-m','v0.1.0.1: missing Agent')
            if rejected.returncode and 'NtCreateDirectoryObject' in rejected.stderr and '0xC0000022' in rejected.stderr:
                self.skipTest('Git MSYS hook launch denied: NtCreateDirectoryObject 0xC0000022; no hook bypass attempted')
            self.assertNotEqual(rejected.returncode,0);self.assertIn('Agent',rejected.stderr)
            initial=git('commit','-m','v0.1.0.1: synthetic initial','-m','Agent: codex')
            self.assertEqual(initial.returncode,0,initial.stderr)
            self.assertEqual(script('check-head').returncode,0)
            self.assertEqual((repo/'VERSION').read_text().strip(),'0.1.0.1')
            # A higher counter on another local ref also contributes to the next W.
            self.assertEqual(git('switch','-c','synthetic-other').returncode,0)
            (repo/'note.txt').write_text('synthetic branch\n')
            self.assertEqual(git('add','note.txt').returncode,0)
            second=git('commit','-m','v0.1.0.2: synthetic other','-m','Agent: codex')
            self.assertEqual(second.returncode,0,second.stderr)
            self.assertEqual(git('switch','main').returncode,0)
            (repo/'VERSION').write_text('0.2.0.0\n',encoding='utf-8')
            (repo/'note.txt').write_text('synthetic main\n')
            self.assertEqual(git('add','VERSION','note.txt').returncode,0)
            third=git('commit','-m','v0.2.0.3: synthetic main','-m','Agent: codex')
            self.assertEqual(third.returncode,0,third.stderr)
            self.assertEqual(script('check-head').returncode,0)
            self.assertEqual((repo/'VERSION').read_text().strip(),'0.2.0.3')
            self.assertIn('v0.2.0.3',(repo/'README.md').read_text(encoding='utf-8'))
