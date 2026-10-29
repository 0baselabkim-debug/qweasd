from unittest.mock import Mock
import pytest
from collector_v1.core import Store, Failure
from collector_v1.providers import Providers
from collector_v1.kis_manual import ManualAPI
from collector_v1.experiment.engine import ProvisionalAPI
from collector_v1.experiment.storage import Metrics


@pytest.mark.parametrize('factory', [Providers, ManualAPI, ProvisionalAPI])
def test_default_real_transport_requires_session_before_counting_http(tmp_path,factory):
    budget=Mock();store=Store(tmp_path/'state')
    api=factory(store,budget) if factory is Providers else factory(store,budget,Metrics())
    api.opener=Mock(side_effect=AssertionError('No actual network permitted'))
    with pytest.raises(Failure,match='shared_session_not_installed'):
        api.http('KIS','/oauth2/tokenP','https://openapi.koreainvestment.com:9443/oauth2/tokenP',auth=True)
    api.opener.assert_not_called();budget.before.assert_not_called()
