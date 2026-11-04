"""Normal self-release for known-closed work; never recovers another owner."""
from contextlib import contextmanager
from kis_shared.runtime import configured
from kis_shared.lease import Blocked
from ..core import Failure,Stop
from ..experiment.storage import events


def auth_rejected(apis):
    for api in reversed(apis):
        budget=api.budget
        if hasattr(budget,'entries'):
            entries=budget.entries
            if not entries:continue
            last=entries[-1];current=budget.current or {}
            return (current.get('endpoint')=='/oauth2/tokenP' and last.get('event')=='finished'
                and last.get('status')=='http_failure' and last.get('http') in (401,403))
        rows=getattr(budget,'ledger',[])
        if rows:
            last=rows[-1]
            return last.get('endpoint')=='/oauth2/tokenP' and last.get('status')=='http_failure' and last.get('http') in (401,403)
    return False


def discard_for_normal_exit(session,apis):
    uncertain_auth=(session.auth_used and session.token_deadline is None and not auth_rejected(apis))
    pending=(session.network_inflight or session.dispatch_ticket is not None
        or session.transport_pending is not None or uncertain_auth)
    if pending:session.uncertain=True
    # Scan while access tokens are still available, then discard before release.
    # A sticky failure survives the loss of those in-memory token strings.
    from . import runner
    from ..core import Store
    store=Store(runner.ROOT)
    try:
        if store.read('failure.json',{}).get('reason')=='credential_in_regular_output':
            session.uncertain=True
            raise Failure('credential_in_regular_output')
        runner.scan(runner.ROOT,apis)
    except Failure as error:
        session.uncertain=True
        store.write('failure.json',dict(status='FAILED',reason=error.code))
        raise
    finally:
        for api in apis:api.token=None
    return 'KEEP_UNRESOLVED' if pending or session.uncertain else 'NORMAL_OWNED_RELEASE'


@contextmanager
def controlled_session(path,apis):
    error=None
    with configured(path) as session:
        try:yield session
        except (Failure,Stop,Blocked) as exc:
            disposition=discard_for_normal_exit(session,apis)
            if disposition!='NORMAL_OWNED_RELEASE':raise
            error=exc  # Raise only AFTER the normal owner-checked context exit.
        finally:discard_for_normal_exit(session,apis)
    if error is not None:raise error
