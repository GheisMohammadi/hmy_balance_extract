package main

import (
	"bytes"
	"fmt"
	"math/big"
	"time"
)

const woneAddress = "0xcf664087a5bb0237a0bad6742852ec6c8d69a27a"
const woneCodeHash = "0x940523b11cbb49f28cdb9798f3031179bc9ef4a309dfd82e21281c61aacc3bd8"

type account struct {
	balance    *big.Int
	nonce      uint64
	root, code []byte
}

func accountValue(raw []byte) account {
	f := fields(decode(raw), 4)
	a := account{balance: integer(f[1]), nonce: number(f[0]), root: scalar(f[2]), code: scalar(f[3])}
	require(len(a.root) == 32 && len(a.code) == 32, "invalid account hashes")
	return a
}
func readAccount(t patricia, address []byte) (account, bool) {
	raw, ok := t.get(keccak(address))
	if !ok {
		return account{balance: new(big.Int), root: emptyTrie, code: keccak(nil)}, false
	}
	return accountValue(raw), true
}
func loadCode(db kvReader, hash []byte) ([]byte, bool) {
	// vc stores validator wrappers; c stores EVM code. The bare-hash format
	// supports either type, so identify wrappers by their RLP shape.
	for _, prefix := range []string{"vc", "c", ""} {
		raw, ok := db.get(append([]byte(prefix), hash...))
		if ok {
			require(bytes.Equal(keccak(raw), hash), "code content hash mismatch")
			return raw, prefix == "vc"
		}
	}
	fail("missing code for nonempty account hash %s", hx(hash))
	return nil, false
}
func possibleWrapper(raw []byte) (x item, ok bool) {
	defer func() {
		if p := recover(); p != nil {
			if _, expected := p.(auditError); !expected {
				panic(p)
			}
			ok = false
		}
	}()
	x = decode(raw)
	if !x.list || len(x.children) != 4 {
		return x, false
	}
	v := x.children[0]
	return x, v.list && len(v.children) == 9 && !v.children[0].list && len(v.children[0].data) == 20 && x.children[1].list
}
func add(row map[string]string, field string, n *big.Int) {
	v, ok := new(big.Int).SetString(row[field], 10)
	require(ok, "uninitialized component")
	row[field] = v.Add(v, n).String()
}
func processWrapper(raw, key []byte, prefixed bool, r *report) {
	x, ok := possibleWrapper(raw)
	if !ok {
		require(!prefixed, "malformed validator wrapper")
		return
	}
	f := fields(x, 4)
	v := fields(f[0], 9)
	validator := scalar(v[0])
	require(bytes.Equal(keccak(validator), key), "validator identity does not match state leaf")
	require(f[1].list, "invalid delegations")
	seen := map[string]bool{}
	for _, entry := range f[1].children {
		d := fields(entry, 4)
		addr := scalar(d[0])
		require(len(addr) == 20, "invalid delegator address")
		s := hx(addr)
		require(!seen[s], "duplicate delegator in wrapper")
		seen[s] = true
		principal, reward := integer(d[1]), integer(d[2])
		require(d[3].list, "invalid undelegations")
		pending := new(big.Int)
		for _, u := range d[3].children {
			uf := fields(u, 2)
			pending.Add(pending, integer(uf[0]))
			integer(uf[1])
		}
		if row, selected := r.Accounts[s]; selected {
			field := "delegated_atto"
			if bytes.Equal(addr, validator) {
				field = "self_stake_atto"
			}
			add(row, field, principal)
			add(row, "unclaimed_reward_atto", reward)
			add(row, "pending_undelegation_atto", pending)
		}
		r.Counts["delegations_scanned"]++
	}
	r.Counts["validators_scanned"]++
}
func extractState(db *database, r *report, withStaking bool) {
	t := patricia{db, unhex(r.Cutoff.Root, 32)}
	for _, s := range sortedAddresses(r.Accounts) {
		addr := unhex(s, 20)
		a, exists := readAccount(t, addr)
		for field, value := range accountMetadata(a, exists, addr, r.Shard) {
			r.Accounts[s][field] = value
		}
	}
	if r.Shard != 0 {
		return
	}
	w, exists := readAccount(t, unhex(woneAddress, 20))
	require(exists && hx(w.code) == woneCodeHash, "unexpected WONE contract code hash")
	loadCode(db, w.code)
	// This code hash pins WETH9-compatible balanceOf(address) mapping at slot 3.
	// Mapping key = keccak(pad32(address)||pad32(3)); secure trie hashes it again.
	storage := patricia{db, w.root}
	for _, s := range sortedAddresses(r.Accounts) {
		preimage := make([]byte, 64)
		copy(preimage[12:32], unhex(s, 20))
		preimage[63] = 3
		raw, ok := storage.get(keccak(keccak(preimage)))
		value := new(big.Int)
		if ok {
			value = integer(decode(raw))
			require(value.BitLen() <= 256, "invalid storage uint256")
		}
		r.Accounts[s]["wone_balance_raw_atto"] = value.String()
	}
	if !withStaking {
		return
	}
	for _, row := range r.Accounts {
		for _, field := range []string{"self_stake_atto", "delegated_atto", "pending_undelegation_atto", "unclaimed_reward_atto"} {
			row[field] = "0"
		}
	}
	last := time.Now()
	t.walk(func(key, raw []byte) {
		check(db.ctx.Err())
		r.Counts["state_leaves_scanned"]++
		a := accountValue(raw)
		if !bytes.Equal(a.code, keccak(nil)) {
			code, prefixed := loadCode(db, a.code)
			processWrapper(code, key, prefixed, r)
		}
		if time.Since(last) > 10*time.Second {
			fmt.Printf("state leaves=%d validators=%d\n", r.Counts["state_leaves_scanned"], r.Counts["validators_scanned"])
			last = time.Now()
		}
	})
}

// Exercise wrapper decoding on a bounded validator list without claiming that
// this list covers the account's delegations to every validator at the cutoff.
func probeStaking(db *database, r *report, validators [][]byte) {
	t := patricia{db, unhex(r.Cutoff.Root, 32)}
	for _, row := range r.Accounts {
		for _, field := range []string{"self_stake_atto", "delegated_atto", "pending_undelegation_atto", "unclaimed_reward_atto"} {
			row[field] = "0"
		}
	}
	for _, addr := range validators {
		a, ok := readAccount(t, addr)
		require(ok, "probe validator not in cutoff state")
		code, _ := loadCode(db, a.code)
		processWrapper(code, keccak(addr), true, r)
	}
	r.Limitations = append(r.Limitations, "staking amounts cover supplied probe validators only")
}

// Metadata accompanies measured components so the standalone writer can emit
// the reference CSV schema without consulting another team's exports.
func accountMetadata(a account, exists bool, address []byte, shard int) map[string]string {
	row := map[string]string{"secure_key": hx(keccak(address)), "address": checksumAddress(address),
		fmt.Sprintf("liquid_shard%d_atto", shard):    a.balance.String(),
		fmt.Sprintf("account_exists_shard%d", shard): fmt.Sprint(exists),
		fmt.Sprintf("nonce_shard%d", shard):          "", fmt.Sprintf("code_hash_shard%d", shard): ""}
	if exists {
		row[fmt.Sprintf("nonce_shard%d", shard)] = fmt.Sprint(a.nonce)
		row[fmt.Sprintf("code_hash_shard%d", shard)] = hx(a.code)
	}
	return row
}
func checksumAddress(address []byte) string {
	require(len(address) == 20, "invalid address length")
	lower := hx(address)[2:]
	hash := keccak([]byte(lower))
	out := []byte(lower)
	for i, c := range out {
		n := hash[i/2] >> 4
		if i%2 == 1 {
			n = hash[i/2] & 15
		}
		if c >= 'a' && c <= 'f' && n >= 8 {
			out[i] = c - 32
		}
	}
	return "0x" + string(out)
}
