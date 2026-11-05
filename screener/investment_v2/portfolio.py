"""User-confirmed fills and append-only thesis/defense history, outside the repo."""
from contextlib import contextmanager
from decimal import Decimal
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import tempfile

from screener.decision_support.evidence import encoded, sha, timestamp
from .contracts import independent, number, amount, symbol

KINDS = {'OPENING_BALANCE', 'DEPOSIT', 'WITHDRAW', 'BUY', 'SELL', 'THESIS', 'DEFENSE', 'DEFENSE_TRIGGER'}


class PortfolioLedger:
    def __init__(self, directory):
        self.root = independent(directory)

    def events(self, expected_head=None):
        if not self.root.exists():
            if expected_head:
                raise ValueError('portfolio_expected_history_missing')
            return []
        rows, previous = [], None
        for sequence, p in enumerate(sorted(self.root.glob('*.json'))):
            if p.is_symlink():
                raise ValueError('portfolio_symlink')
            doc = json.loads(p.read_bytes())
            payload = {k: v for k, v in doc.items() if k != 'hash'}
            if (doc['sequence'], doc['previous_hash'], doc['hash']) != (sequence, previous, sha(encoded(payload))):
                raise ValueError('portfolio_chain_corrupt')
            if p.name != f'{sequence:08d}-{doc["hash"]}.json':
                raise ValueError('portfolio_filename_mismatch')
            rows.append(doc)
            previous = doc['hash']
        if len({r['event']['id'] for r in rows}) != len(rows):
            raise ValueError('duplicate_portfolio_event_id')
        if expected_head and expected_head not in {r['hash'] for r in rows}:
            raise ValueError('portfolio_expected_history_missing')
        return rows

    def append(self, event):
        """No market requests or inferred fills. A lock and hard link publish one file."""
        if event.get('kind') not in KINDS or not isinstance(event.get('id'), str) or not event['id']:
            raise ValueError('invalid_portfolio_event')
        at = timestamp(event['at'])
        if at > dt.datetime.now(dt.timezone.utc):
            raise ValueError('portfolio_event_in_future')
        if event['kind'] != 'DEFENSE_TRIGGER' and event.get('user_confirmed') is not True:
            raise ValueError('explicit_user_confirmation_required')
        if event['kind'] in ('BUY', 'SELL', 'THESIS', 'DEFENSE', 'DEFENSE_TRIGGER'):
            symbol(event['stock_code'])
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root/'writer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = self.events()
            for row in rows:
                if row['event']['id'] == event['id']:
                    if encoded(row['event']) != encoded(event):
                        raise ValueError('portfolio_event_id_reused_with_changed_content')
                    return row
            if rows and at < timestamp(rows[-1]['event']['at']):
                raise ValueError('portfolio_history_cannot_be_backdated')
            candidate = dict(sequence=len(rows), previous_hash=rows[-1]['hash'] if rows else None, event=event)
            candidate['hash'] = sha(encoded(candidate))
            # Validate balances, quantities and defense transitions before writing.
            replay([*rows, candidate], event['at'])
            raw = encoded(candidate)
            fd, temporary = tempfile.mkstemp(prefix='.event-', dir=self.root)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(temporary, self.root/f'{len(rows):08d}-{candidate["hash"]}.json')
            finally:
                os.unlink(temporary)
            return candidate

    def snapshot(self, cutoff, expected_head=None):
        return replay(self.events(expected_head), cutoff)


def replay(rows, cutoff):
    cash, holdings, realized = Decimal(0), {}, Decimal(0)
    included = []
    for row in rows:
        event = row['event']
        if timestamp(event['at']) > timestamp(cutoff):
            continue
        included.append(row['hash'])
        kind = event['kind']
        if kind == 'OPENING_BALANCE':
            if len(included) != 1 or holdings:
                raise ValueError('opening_balance_must_be_first_event')
            cash = number(event['cash_krw'])
            for given in event['positions']:
                code = symbol(given['stock_code'])
                qty, average = given['quantity'], number(given['average_cost_krw'])
                if code in holdings or type(qty) is not int or qty <= 0 or average <= 0 or given.get('market') not in ('KOSPI', 'KOSDAQ') or not given.get('stock_name'):
                    raise ValueError('invalid_user_opening_position')
                holdings[code] = dict(stock_code=code, stock_name=given['stock_name'], market=given['market'],
                    quantity=qty, cost=qty*average, realized=Decimal(0), trades=[], trade_history=[], thesis=None,
                    defense=None, defense_history=[], defense_triggered=False, lifecycle=1, closed_lifecycles=[],
                    opening_basis='USER_DECLARED_AVERAGE_NOT_INFERRED_TRADE')
        elif kind in ('DEPOSIT', 'WITHDRAW'):
            x = number(event['amount_krw'])
            if x <= 0:
                raise ValueError('positive_cash_movement_required')
            cash += x if kind == 'DEPOSIT' else -x
        else:
            code = event['stock_code']
            position = holdings.setdefault(code, dict(stock_code=code, stock_name=None, market=None,
                quantity=0, cost=Decimal(0), realized=Decimal(0), trades=[], thesis=None,
                defense=None, defense_history=[], defense_triggered=False, lifecycle=1,
                closed_lifecycles=[], trade_history=[]))
            if kind in ('BUY', 'SELL'):
                qty, price = event['quantity'], number(event['price_krw'])
                fee, tax = number(event['fee_krw']), number(event['tax_krw'])
                if type(qty) is not int or qty <= 0 or price <= 0 or min(fee, tax) < 0:
                    raise ValueError('invalid_fill_quantity_price_fee_tax')
                if not event.get('stock_name') or event.get('market') not in ('KOSPI', 'KOSDAQ'):
                    raise ValueError('user_stock_name_and_market_required')
                if position['market'] not in (None, event['market']):
                    raise ValueError('portfolio_market_changed')
                position.update(stock_name=event['stock_name'], market=event['market'])
                if kind == 'BUY':
                    if position['quantity'] == 0 and position['trades']:
                        position['closed_lifecycles'].append(dict(lifecycle=position['lifecycle'],
                            trades=list(position['trades']), thesis=position['thesis'],
                            defense_history=list(position['defense_history']), defense_triggered=position['defense_triggered']))
                        position.update(lifecycle=position['lifecycle']+1, trades=[], thesis=None,
                                        defense=None, defense_history=[], defense_triggered=False)
                    total = qty*price+fee+tax
                    cash -= total
                    position['cost'] += total
                    position['quantity'] += qty
                else:
                    if qty > position['quantity']:
                        raise ValueError('cannot_sell_unconfirmed_or_unowned_shares')
                    basis = position['cost']*qty/position['quantity']
                    proceeds = qty*price-fee-tax
                    pnl = proceeds-basis
                    cash += proceeds
                    position['cost'] -= basis
                    position['quantity'] -= qty
                    position['realized'] += pnl
                    realized += pnl
                position['trades'].append(event)
                position['trade_history'].append(event)
            elif kind == 'THESIS':
                if not event.get('text') or not event.get('evidence_ids'):
                    raise ValueError('thesis_and_evidence_required')
                position['thesis'] = event
            elif kind == 'DEFENSE':
                level = number(event['price_krw'])
                if level <= 0 or not event.get('reason') or not event.get('evidence_ids'):
                    raise ValueError('defense_requires_structure_reason_and_evidence')
                if position['defense_triggered']:
                    raise ValueError('triggered_defense_cannot_be_retroactively_replaced')
                if position['defense'] and level < number(position['defense']['price_krw']):
                    raise ValueError('defense_cannot_be_lowered_to_erase_risk')
                position['defense'] = event
                position['defense_history'].append(event)
            elif kind == 'DEFENSE_TRIGGER':
                if not position['defense'] or not event.get('evidence_ids') or event.get('origin') != 'VERIFIED_OBSERVATION':
                    raise ValueError('defense_trigger_requires_existing_rule_and_evidence')
                if number(event['observed_price_krw']) > number(position['defense']['price_krw']):
                    raise ValueError('defense_trigger_not_observed')
                position['defense_triggered'] = True
        if cash < 0:
            raise ValueError('negative_cash_requires_explicit_supported_account_model')
    for p in holdings.values():
        p['average_cost_krw'] = amount(p['cost']/p['quantity']) if p['quantity'] else None
        p['cost_basis_krw'] = amount(p.pop('cost'))
        p['realized_pnl_krw'] = amount(p.pop('realized'))
    return dict(status='USER_CONFIRMED', cutoff=cutoff, cash_krw=amount(cash), realized_pnl_krw=amount(realized),
                holdings=holdings, included_event_hashes=included, head_hash=included[-1] if included else None,
                realized_pnl_scope='SINCE_LEDGER_START_PRIOR_REALIZED_UNKNOWN')


def review(snapshot, evidence):
    if snapshot is None:
        return dict(status='NOT_RUN', reason='user_account_and_fills_not_provided', holdings={}, assets_krw=None,
                    total_planned_risk_ratio=None, constraints=[])
    marks, positions = {}, {}
    cash = number(snapshot['cash_krw'])
    complete, total, risks = True, cash, []
    for code, p in snapshot['holdings'].items():
        if not p['quantity']:
            continue
        row = evidence.get(code)
        mark = None
        if row and row['market'] == p['market'] and row['data_quality']['latest_price_verified']:
            m = row.get('price_trend_metrics', {}).get('close', {})
            if m.get('status') == 'valid' and m.get('value') is not None:
                mark = number(m['value'])
        marks[code] = mark
        complete &= mark is not None
        if mark is not None:
            total += p['quantity']*mark
    assets = total if complete else None
    constraints = []
    for code, mark in marks.items():
        p = snapshot['holdings'][code]
        value_krw = p['quantity']*mark if mark is not None else None
        defense = number(p['defense']['price_krw']) if p['defense'] else None
        risk = p['quantity']*max(Decimal(0), mark-defense) if mark is not None and defense is not None else None
        risks.append(risk)
        weight = value_krw/assets if value_krw is not None and assets and assets > 0 else None
        if weight is not None and weight > Decimal('.40'):
            constraints.append(dict(stock_code=code, reason='MAXIMUM_40_PERCENT_EXCEEDED'))
        positions[code] = dict(**p, mark_krw=amount(mark) if mark is not None else None,
            valuation_asof=evidence[code]['asof'] if mark is not None else None,
            market_value_krw=amount(value_krw) if value_krw is not None else None,
            unrealized_pnl_krw=amount(value_krw-number(p['cost_basis_krw'])) if value_krw is not None else None,
            weight_ratio=amount(weight) if weight is not None else None,
            planned_risk_krw=amount(risk) if risk is not None else None,
            defense_breached=p['defense_triggered'] or (mark is not None and defense is not None and mark <= defense))
    if len(positions) > 4:
        constraints.append(dict(reason='MORE_THAN_FOUR_POSITIONS'))
    total_risk = sum(risks)/assets if all(r is not None for r in risks) and assets and assets > 0 else None
    if total_risk is not None and total_risk > Decimal('.03'):
        constraints.append(dict(reason='TOTAL_REFERENCE_RISK_3_PERCENT_EXCEEDED'))
    return dict(status='VALUED' if complete else 'UNAVAILABLE_MARKS', holdings=positions, cash_krw=snapshot['cash_krw'],
                head_hash=snapshot['head_hash'],
                assets_krw=amount(assets) if assets is not None else None,
                total_planned_risk_ratio=amount(total_risk) if total_risk is not None else None, constraints=constraints,
                policy=dict(max_positions=4, basic_weight='20-30%', conviction_weight='30-35%', maximum_weight='40%',
                            reference_trade_risk='1%', reference_total_risk='3%'))


def sizing(portfolio, code, entry, defense):
    if portfolio.get('assets_krw') is None or portfolio.get('total_planned_risk_ratio') is None:
        return dict(status='UNAVAILABLE', quantity=None, reason='account_assets_or_existing_risk_unknown')
    entry, defense = number(entry), number(defense)
    if not 0 < defense < entry:
        raise ValueError('positive_entry_risk_required')
    assets = number(portfolio['assets_krw'])
    old = portfolio['holdings'].get(code, {})
    if assets <= 0 or (code not in portfolio['holdings'] and len(portfolio['holdings']) >= 4):
        return dict(status='INAPPLICABLE', quantity=0, reason='no_available_position_slot_or_assets')
    risk_room = min(assets*Decimal('.01'), assets*(Decimal('.03')-number(portfolio['total_planned_risk_ratio'])))
    weight_room = assets*Decimal('.40')-number(old.get('market_value_krw') or 0)
    qty = max(0, min(int(risk_room/(entry-defense)), int(weight_room/entry), int(number(portfolio['cash_krw'])/entry)))
    return dict(status='REFERENCE_ONLY_NOT_ORDER', quantity=qty, reason=None, assumed_entry_krw=amount(entry),
                fees_not_estimated=True, requires_user_fill_confirmation=True)
