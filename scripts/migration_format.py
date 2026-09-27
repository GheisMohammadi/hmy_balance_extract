"""Independent serialization of the harmony-migration gross-claim CSV contract.

Only column definitions are pinned from the reference repository. No reference
calculation modules are imported and no team balances are used as inputs.
"""
import csv
import json
import re
from pathlib import Path
from evidence_io import atomic_text, strict_csv

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas/harmony-migration-claims-v1.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text())
NATIVE_FIELDS = tuple(SCHEMA['native_fields'])
MIGRATION_FIELDS = tuple(SCHEMA['migration_fields'])


def fieldnames(variant):
    if variant not in ('native', 'migration'):
        raise ValueError('claims format must be native or migration')
    return NATIVE_FIELDS if variant == 'native' else MIGRATION_FIELDS


def fixed(value, decimals=18):
    if type(value) is not int or value < 0:
        raise ValueError('claim amounts must be non-negative integers')
    whole, fraction = divmod(value, 10**decimals)
    return f'{whole}.{fraction:0{decimals}d}'


def identity(row):
    m = row.get('_metadata')
    if not isinstance(m, dict):
        raise ValueError('state evidence lacks identity/nonce/code metadata; regenerate state reports with the current extractor')
    key, addr = m.get('secure_key', ''), m.get('address', '')
    if not re.fullmatch(r'0x[0-9a-f]{64}', key):
        raise ValueError('missing or invalid extracted secure key')
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', addr) or addr.lower() != row['eth_address']:
        raise ValueError('extracted identity differs from selected address')
    for shard in (0, 1):
        exists = m.get(f'account_exists_shard{shard}')
        nonce, code = m.get(f'nonce_shard{shard}'), m.get(f'code_hash_shard{shard}')
        if exists not in ('true', 'false'):
            raise ValueError('unknown state metadata must not be represented as absence')
        if int(row[f'liquid_shard{shard}_atto']) > 0 and exists != 'true':
            raise ValueError('positive liquid balance conflicts with extracted account absence')
        if exists == 'true':
            if not isinstance(nonce, str) or not re.fullmatch(r'0|[1-9][0-9]*', nonce):
                raise ValueError('missing or invalid extracted nonce')
            if not isinstance(code, str) or not re.fullmatch(r'0x[0-9a-f]{64}', code):
                raise ValueError('missing or invalid extracted code hash')
        elif exists != 'false' or nonce != '' or code != '':
            raise ValueError('unknown state metadata must not be represented as absence')
    return m


def rows_for_claims(rows, compute, threshold, manifest, variant='migration'):
    """Return strictly secure-key-sorted gross claim rows and omitted selections."""
    fields = fieldnames(variant)
    valuation = manifest['valuation']
    price = valuation['usd_per_one']
    if not isinstance(price, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', price):
        raise ValueError('manifest price must be an exact non-negative decimal string')
    whole, _, fraction = price.partition('.')
    price_scale, numerator = len(fraction), int(whole + fraction)
    result, omitted, seen_addresses, seen_keys = [], [], set(), set()
    for raw in rows:
        c = compute(raw, 0, threshold)
        # All native-positive rows exist in all-address-native-claims; WONE-only
        # rows enter all-address-migration-claims only when WONE is deliverable.
        included = c['native_total_atto'] > 0 or (variant == 'migration' and c['wone_airdrop_atto'] > 0)
        if raw['eth_address'] in seen_addresses:
            raise ValueError('duplicate selected address')
        seen_addresses.add(raw['eth_address'])
        if not included:
            omitted.append(raw['eth_address'])
            continue
        meta = identity(raw)
        if meta['secure_key'] in seen_keys:
            raise ValueError('duplicate extracted secure key')
        seen_keys.add(meta['secure_key'])
        values = {
            'liquid_shard0': c['liquid_shard0_atto'],
            'liquid_shard1': c['liquid_shard1_atto'],
            'liquid_total': c['liquid_shard0_atto'] + c['liquid_shard1_atto'],
            'active_staked_or_delegated': c['self_stake_atto'] + c['delegated_atto'],
            'pending_undelegation': c['pending_undelegation_atto'],
            'unclaimed_staking_reward': c['unclaimed_reward_atto'],
            'pending_cross_shard': c['pending_cross_shard_atto'],
            'wallet_airdrop': c['native_wallet_atto'] if variant == 'native' else c['wallet_airdrop_atto'],
            'staked_to_vault': c['staked_atto'],
            'total_claim': c['native_total_atto'] if variant == 'native' else c['total_claim_atto'],
        }
        if variant == 'migration':
            values.update(native_wallet_airdrop=c['native_wallet_atto'],
                          wone_balance=c['wone_balance_atto'], wone_airdrop=c['wone_airdrop_atto'],
                          qualification_total=c['qualification_atto'], native_total_claim=c['native_total_atto'])
        row = {
            'secure_key': meta['secure_key'], 'address': meta['address'],
            'address_or_secure_key': meta['address'], 'address_resolved': 'true',
            'claims_shard0_block': str(manifest['cutoff']['shard0']['block']),
            'claims_shard1_block': str(manifest['cutoff']['shard1']['block']),
            'valuation_price_reference_shard0_block': str(valuation['reference_shard0_block']),
            'valuation_price_usd_per_one': price,
            'wallet_airdrop_usd': fixed(values['wallet_airdrop'] * numerator, 18 + price_scale),
            'total_usd': fixed(values['total_claim'] * numerator, 18 + price_scale),
        }
        for component, value in values.items():
            row[component + '_atto'] = str(value)
            row[component + '_one'] = fixed(value)
        for shard in (0, 1):
            # The native liquid ledger contains only positive balances, hence
            # blank metadata for a shard with no positive liquid row. WONE-only
            # recipient rows additionally include cutoff shard-0 metadata.
            metadata_present = c[f'liquid_shard{shard}_atto'] > 0 or (
                variant == 'migration' and c['native_total_atto'] == 0 and shard == 0)
            row[f'nonce_shard{shard}'] = meta[f'nonce_shard{shard}'] if metadata_present else ''
            row[f'code_hash_shard{shard}'] = meta[f'code_hash_shard{shard}'] if metadata_present else ''
        result.append({field: row[field] for field in fields})
    result.sort(key=lambda row: row['secure_key'])
    return result, sorted(omitted)


def write_claims(path, rows, variant, replace=False):
    """Publish a complete CSV, without overwriting an existing result by default."""
    with atomic_text(path, replace) as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames(variant), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def compare_claims(path, rows, compute, threshold, manifest, max_examples):
    """Compare canonical native or WONE claims, without borrowing team inputs."""
    with Path(path).open(encoding='utf-8', newline='') as f:
        headers, reader = strict_csv(f)
        variant = 'migration' if any(k in headers for k in ('wone_balance_atto', 'native_total_claim_atto')) else 'native'
        fields = fieldnames(variant)
        if len(headers) != len(set(headers)) or not set(fields) <= set(headers):
            raise ValueError(f'team {variant} claims CSV is missing canonical columns or has duplicate headers')
        expected_rows, omitted = rows_for_claims(rows, compute, threshold, manifest, variant)
        expected = {r['address'].lower(): r for r in expected_rows}
        selected = {r['eth_address'] for r in rows}
        seen, unexpected, examples, count = set(), [], [], 0
        for line, row in enumerate(reader, 2):
            address = row['address'].lower()
            if address not in selected:
                continue
            if address in seen:
                raise ValueError(f'duplicate selected team address at line {line}')
            seen.add(address)
            if address not in expected:
                unexpected.append(address)
                continue
            for field in fields:
                actual, wanted = row[field], expected[address][field]
                if field in ('address', 'address_or_secure_key'):
                    actual, wanted = actual.lower(), wanted.lower()
                if actual != wanted:
                    count += 1
                    if len(examples) < max_examples:
                        examples.append({'eth_address': address, 'field': field,
                                         'independent': wanted, 'team': actual})
    return {'format': f'harmony-migration-{variant}', 'mismatches': count,
            'examples': examples, 'truncated': count > len(examples),
            'missing_addresses': sorted(set(expected) - seen),
            'unexpected_selected_addresses': sorted(unexpected),
            'selected_accounts_without_claim_rows': omitted,
            'compared_addresses': len(set(expected) & seen), 'compared_fields': list(fields),
            'additional_team_fields_not_compared': sorted(set(headers) - set(fields)),
            'scope': 'gross claim CSV; reviewed net allocations are in the separate audit output'}
