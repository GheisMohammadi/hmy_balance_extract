package main

import (
	"bytes"
	"testing"
)

func TestIndependentValidatorComponents(t *testing.T) {
	validator := bytes.Repeat([]byte{1}, 20)
	other := bytes.Repeat([]byte{2}, 20)
	v := rlpList(rlpBytes(validator), rlpList(), rlpNum(0), rlpNum(0), rlpNum(0), rlpNum(0), rlpList(), rlpList(), rlpNum(0))
	self := rlpList(rlpBytes(validator), rlpNum(100), rlpNum(7), rlpList(rlpList(rlpNum(9), rlpNum(2))))
	delegated := rlpList(rlpBytes(other), rlpNum(300), rlpNum(11), rlpList(rlpList(rlpNum(13), rlpNum(3)), rlpList(rlpNum(17), rlpNum(4))))
	raw := rlpList(v, rlpList(self, delegated), rlpList(), rlpNum(0))
	r := report{Accounts: map[string]map[string]string{}, Counts: map[string]uint64{}}
	for _, a := range [][]byte{validator, other} {
		r.Accounts[hx(a)] = map[string]string{"self_stake_atto": "0", "delegated_atto": "0", "unclaimed_reward_atto": "0", "pending_undelegation_atto": "0"}
	}
	processWrapper(raw, keccak(validator), true, &r)
	if r.Accounts[hx(validator)]["self_stake_atto"] != "100" || r.Accounts[hx(other)]["delegated_atto"] != "300" || r.Accounts[hx(other)]["pending_undelegation_atto"] != "30" || r.Accounts[hx(other)]["unclaimed_reward_atto"] != "11" {
		t.Fatal(r.Accounts)
	}
	mustFail(t, func() { processWrapper(raw, keccak(other), true, &r) })
	duplicate := rlpList(v, rlpList(self, self), rlpList(), rlpNum(0))
	mustFail(t, func() { processWrapper(duplicate, keccak(validator), true, &r) })
	mustFail(t, func() { processWrapper([]byte{1}, keccak(validator), true, &r) })
}

func TestClaimIdentityAndNonceMetadata(t *testing.T) {
	for _, want := range []string{
		"0x52908400098527886E0F7030069857D2E4169EE7",
		"0x8617E340B3D01FA5F11F306F4090FD50E238070D",
		"0xde709f2102306220921060314715629080e2fb77",
	} {
		if got := checksumAddress(unhex(want, 20)); got != want {
			t.Fatalf("checksum %s != %s", got, want)
		}
	}
	address := bytes.Repeat([]byte{1}, 20)
	raw := rlpList(rlpNum(7), rlpNum(123), rlpBytes(emptyTrie), rlpBytes(keccak(nil)))
	row := accountMetadata(accountValue(raw), true, address, 0)
	if row["nonce_shard0"] != "7" || row["liquid_shard0_atto"] != "123" || row["secure_key"] != hx(keccak(address)) || row["code_hash_shard0"] != hx(keccak(nil)) {
		t.Fatal(row)
	}
	absent, _ := readAccount(patricia{memoryDB{}, emptyTrie}, address)
	row = accountMetadata(absent, false, address, 1)
	if row["nonce_shard1"] != "" || row["code_hash_shard1"] != "" || row["account_exists_shard1"] != "false" || row["liquid_shard1_atto"] != "0" {
		t.Fatal(row)
	}
}
