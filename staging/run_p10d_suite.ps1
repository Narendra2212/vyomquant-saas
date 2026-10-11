# Phase 10 / Workstream D — targeted financial, schema, referral and reconciliation suite.
# Records the exact commands and outcomes for the workstream report.
$ErrorActionPreference = 'Continue'
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging\pg_dsn.txt -Raw).Trim()

$groups = [ordered]@{
  'FEE (observation 1)' = @(
    'tests/test_fee_third_asset_pct_is_nullable_postgres.py',
    'tests/test_p10d_fee_summary_does_not_mix_denominations_postgres.py',
    'tests/test_fee_denomination_is_not_assumed.py',
    'tests/test_fee01_tenant_is_validated_not_sentinel_postgres.py',
    'tests/test_fee_precision_fix.py',
    'tests/test_tenant02_fee_records_rls_postgres.py'
  )
  'REFERRAL (observations 2 + 3)' = @(
    'tests/test_referral_payout_schema_contract_postgres.py',
    'tests/test_referral_payout_debit_is_compensated_on_insert_failure.py',
    'tests/test_p10d_referral_settlement_lifecycle_postgres.py',
    'tests/test_referral_money_lifecycle_postgres.py',
    'tests/test_money01_referral_commission_idempotence_postgres.py',
    'tests/test_money04_referral_ledger_is_single_currency_postgres.py',
    'tests/test_money06_referral_obligations_postgres.py',
    'tests/test_referral_missing_tables.py',
    'tests/test_referral_program_decision.py'
  )
  'RECONCILIATION (observation 4)' = @(
    'tests/test_p10d_exchange_reconciliation_fails_safely.py',
    'tests/test_exchange_safety_fix.py',
    'tests/test_exchange_reconciliation.py'
  )
  'SCHEMA CONTRACTS' = @(
    'tests/test_orm_models_match_provisioned_schema.py',
    'tests/test_schema_table_reference_drift.py',
    'tests/test_schema_as_code_completeness.py'
  )
}

foreach ($name in $groups.Keys) {
  $files = $groups[$name] | Where-Object { Test-Path $_ }
  Write-Output ('=' * 78)
  Write-Output ("GROUP: " + $name)
  Write-Output ('=' * 78)
  $out = & python -m pytest @files -p no:randomly -q --no-header -W ignore::DeprecationWarning 2>&1
  $out | Select-String -Pattern 'FAILED|ERROR |^E  |passed|failed|xfail|error' |
      Select-Object -Last 12 | ForEach-Object { $_.ToString() }
  Write-Output ''
}
