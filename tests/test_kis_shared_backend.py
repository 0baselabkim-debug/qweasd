"""Backend failures cannot fall back to cached permission or force an update."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from kis_shared.git_backend import GitBackend, CENTRAL
from kis_shared.lease import Blocked, initial


class BackendTests(unittest.TestCase):
    def backend(self):
        b=object.__new__(GitBackend)
        b.directory=Path('/SYNTHETIC_NOT_OPENED');b.branch='kis-shared-account-state';b.remote=CENTRAL;b._cached=None
        return b
    def test_network_failure_cannot_use_cached_owner(self):
        b=self.backend();b._cached=('a'*40,initial());b._git=Mock(side_effect=Blocked('denied'))
        with self.assertRaises(Blocked):b.read()
    def test_unchanged_fresh_tip_can_use_cached_content(self):
        b=self.backend();state=initial();b._cached=('a'*40,state)
        b._git=Mock(return_value=SimpleNamespace(stdout=('a'*40+'\trefs/heads/'+b.branch+'\n').encode()))
        revision,result=b.read();self.assertEqual(revision,'a'*40);self.assertEqual(result,state)
        result['sequence']=99;self.assertEqual(b._cached[1]['sequence'],0)
        self.assertEqual(b._git.call_args.args[0],'ls-remote')
    def test_ref_mismatch_blocks(self):
        b=self.backend();b._git=Mock(return_value=SimpleNamespace(stdout=('a'*40+'\trefs/heads/OTHER\n').encode()))
        with self.assertRaises(Blocked):b.read()
    def test_push_is_normal_fastforward_never_force(self):
        b=self.backend();state=initial();calls=[]
        def git(*args,**kwargs):
            calls.append(args)
            if args[0]=='push':return SimpleNamespace(returncode=1,stdout=b'')
            return SimpleNamespace(returncode=0,stdout=('b'*40+'\n').encode())
        b._git=git
        self.assertFalse(b.compare_and_append('a'*40,state))
        self.assertEqual(calls[-1][0:2],('push','--porcelain'))
        self.assertFalse(any('force' in arg or arg.startswith('+') for c in calls for arg in c))
        self.assertIn('-p',calls[-2]);self.assertIn('a'*40,calls[-2])
