"""Git's non-fast-forward rejection is the atomic primitive, not Actions groups.

Two children of the same commit cannot both become the branch tip with an
ordinary push. No force, resets, deletion or implicit initialization is allowed.
Repository credentials use the preconfigured normal Git credential helper.
"""
import json
import copy
import re
import subprocess
from pathlib import Path
from .lease import Blocked, validate

CENTRAL = 'https://github.com/0baselabkim-debug/qweasd.git'
BRANCH = 'kis-shared-account-state'


class GitBackend:
    def __init__(self, directory, *, branch=BRANCH, remote=CENTRAL):
        if remote != CENTRAL or not re.fullmatch(r'kis-shared-(account-state|probe-[a-z0-9-]+)', branch):
            raise Blocked('shared_ledger_destination_rejected')
        self.directory = Path(directory)
        self.branch, self.remote = branch, remote
        self._cached = None
        self.directory.mkdir(parents=True, exist_ok=True)
        if not (self.directory/'HEAD').exists():
            self._git('init', '--bare', str(self.directory), cwd=self.directory.parent)

    def _git(self, *args, data=None, cwd=None, reject=False):
        try:
            p = subprocess.run(['git', '-c', 'credential.helper=!gh auth git-credential',
                '-c', 'user.name=KIS shared coordinator', '-c', 'user.email=kis-shared@users.noreply.github.com',
                *args], cwd=cwd or self.directory, input=data, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            raise Blocked('shared_git_transport_unavailable_outcome_unknown') from None
        if p.returncode and not reject:
            raise Blocked('shared_git_operation_failed')
        return p

    def read(self):
        # Never authorize using a cached ref after failed refresh.
        tip = self._git('ls-remote', '--exit-code', self.remote, 'refs/heads/'+self.branch).stdout.decode().split()
        if len(tip) != 2 or not re.fullmatch('[0-9a-f]{40}', tip[0]) or tip[1] != 'refs/heads/'+self.branch:
            raise Blocked('shared_remote_tip_unverified')
        if self._cached is not None and self._cached[0] == tip[0]:
            return self._cached[0], copy.deepcopy(self._cached[1])
        self._git('fetch', '--no-tags', self.remote, 'refs/heads/'+self.branch)
        revision = self._git('rev-parse', 'FETCH_HEAD').stdout.decode().strip()
        try:
            state = json.loads(self._git('show', revision+':lease.json').stdout)
            validate(state)
        except (ValueError, TypeError):
            raise Blocked('shared_ledger_invalid') from None
        self._cached = revision, copy.deepcopy(state)
        return revision, state

    def compare_and_append(self, expected, state):
        if not re.fullmatch('[0-9a-f]{40}', expected):
            raise Blocked('shared_expected_revision_invalid')
        validate(state)
        raw = json.dumps(state, sort_keys=True, separators=(',', ':')).encode()+b'\n'
        blob = self._git('hash-object', '-w', '--stdin', data=raw).stdout.strip()
        tree = self._git('mktree', data=b'100644 blob '+blob+b'\tlease.json\n').stdout.strip().decode()
        commit = self._git('commit-tree', tree, '-p', expected, data=b'KIS shared lease transition\n').stdout.strip().decode()
        result = self._git('push', '--porcelain', self.remote, commit+':refs/heads/'+self.branch, reject=True)
        if result.returncode:
            return False  # includes ambiguous push; caller cannot dispatch
        actual, saved = self.read()
        return actual == commit and saved == state
