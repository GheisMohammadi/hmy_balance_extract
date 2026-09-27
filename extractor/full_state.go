package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type candidateFlags []string

func (f *candidateFlags) String() string     { return strings.Join(*f, ",") }
func (f *candidateFlags) Set(v string) error { *f = append(*f, v); return nil }

// Only addresses are taken from optional discovery hints. Identity is checked
// against the secure trie key and every amount is read from the raw database.
type identities struct {
	addresses map[[32]byte][20]byte
	slots     map[[32]byte][20]byte
}

func newIdentities() *identities {
	return &identities{map[[32]byte][20]byte{}, map[[32]byte][20]byte{}}
}
func hashArray(b []byte) [32]byte {
	require(len(b) == 32, "invalid hash length")
	var v [32]byte
	copy(v[:], b)
	return v
}
func (i *identities) add(address []byte) {
	require(len(address) == 20, "invalid candidate address")
	var a [20]byte
	copy(a[:], address)
	key := hashArray(keccak(address))
	if old, ok := i.addresses[key]; ok {
		require(old == a, "address hash collision")
		return
	}
	i.addresses[key] = a
	preimage := make([]byte, 64)
	copy(preimage[12:32], address)
	preimage[63] = 3
	slot := hashArray(keccak(keccak(preimage)))
	if old, ok := i.slots[slot]; ok {
		require(old == a, "storage-key collision")
	}
	i.slots[slot] = a
}
func (i *identities) resolve(db kvReader, key []byte) ([]byte, bool) {
	if a, ok := i.addresses[hashArray(key)]; ok {
		return a[:], true
	}
	b, ok := db.get(append([]byte("secure-key-"), key...))
	if !ok {
		return nil, false
	}
	require(len(b) == 20 && bytes.Equal(keccak(b), key), "invalid account address preimage")
	i.add(b)
	return b, true
}
func loadCandidates(db *database, ids *identities, paths []string) []map[string]any {
	var provenance []map[string]any
	for _, path := range paths {
		f, e := os.Open(path)
		check(e)
		h := sha256.New()
		r := csv.NewReader(io.TeeReader(f, h))
		header, e := r.Read()
		if e != nil {
			f.Close()
			check(e)
		}
		col := -1
		for n, v := range header {
			if v == "address" || v == "eth_address" {
				require(col == -1, "ambiguous candidate address columns")
				col = n
			}
		}
		require(col >= 0, "candidate CSV requires address or eth_address column")
		var count uint64
		for {
			check(db.ctx.Err())
			row, e := r.Read()
			if e == io.EOF {
				break
			}
			if e != nil {
				f.Close()
				check(e)
			}
			value := strings.TrimSpace(row[col])
			if value != "" {
				ids.add(unhex(value, 20))
				count++
			}
		}
		check(f.Close())
		provenance = append(provenance, map[string]any{"path": path, "sha256": hex.EncodeToString(h.Sum(nil)), "address_rows": count, "role": "address-discovery hints only; all amounts independently read from raw DB"})
		fmt.Printf("loaded candidate addresses rows=%d unique=%d file=%s\n", count, len(ids.addresses), path)
	}
	return provenance
}

type csvArtifact struct {
	file   *os.File
	writer *csv.Writer
	hash   io.Writer
	digest interface{ Sum([]byte) []byte }
	path   string
}

func newArtifact(dir, name string, fields []string) *csvArtifact {
	path := filepath.Join(dir, name)
	f, e := os.OpenFile(path+".partial", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0640)
	check(e)
	h := sha256.New()
	w := csv.NewWriter(io.MultiWriter(f, h))
	check(w.Write(fields))
	return &csvArtifact{file: f, writer: w, hash: h, digest: h, path: path}
}
func (a *csvArtifact) write(row ...string) { check(a.writer.Write(row)) }
func (a *csvArtifact) flush()              { a.writer.Flush(); check(a.writer.Error()) }
func (a *csvArtifact) finish() string {
	a.flush()
	check(a.file.Sync())
	check(a.file.Close())
	check(os.Link(a.path+".partial", a.path))
	check(os.Remove(a.path + ".partial"))
	return hex.EncodeToString(a.digest.Sum(nil))
}
func fullWrapper(db kvReader, key, code []byte, prefixed bool, r *report, ids *identities, active *[][]string) {
	x, ok := possibleWrapper(code)
	if !ok {
		require(!prefixed, "malformed validator wrapper")
		return
	}
	validator := scalar(fields(x.children[0], 9)[0])
	require(bytes.Equal(keccak(validator), key), "validator address mismatch")
	ids.add(validator)
	for _, raw := range x.children[1].children {
		d := fields(raw, 4)
		addr := scalar(d[0])
		ids.add(addr)
		s := hx(addr)
		if _, ok := r.Accounts[s]; !ok {
			r.Accounts[s] = map[string]string{"self_stake_atto": "0", "delegated_atto": "0", "pending_undelegation_atto": "0", "unclaimed_reward_atto": "0"}
		}
		principal := integer(d[1])
		if principal.Sign() > 0 {
			*active = append(*active, []string{checksumAddress(validator), hx(key), checksumAddress(addr), hx(keccak(addr)), principal.String(), fmt.Sprint(bytes.Equal(addr, validator))})
		}
	}
	processWrapper(code, key, prefixed, r)
}
func amount(row map[string]string, field string) *big.Int {
	v, ok := new(big.Int).SetString(row[field], 10)
	require(ok, "invalid component integer")
	return v
}
func fullState(db *database, r *report, dir string, candidates []string) {
	// Write the completion summary after finalizing all CSV files and hashes.
	// A .partial suffix identifies unfinished output.
	check(os.Mkdir(dir, 0750))
	start := time.Now()
	ids := newIdentities()
	discovery := loadCandidates(db, ids, candidates)
	accounts := newArtifact(dir, "accounts.csv", []string{"secure_key", "address", "balance_atto", "nonce", "code_hash", "storage_root"})
	defer accounts.file.Close()
	liquid := newArtifact(dir, "liquid.csv", []string{"secure_key", "address", "balance_atto", "nonce", "code_hash"})
	defer liquid.file.Close()
	unresolved := newArtifact(dir, "unresolved-accounts.csv", []string{"secure_key", "balance_atto"})
	defer unresolved.file.Close()
	t := patricia{db, unhex(r.Cutoff.Root, 32)}
	totalLiquid := new(big.Int)
	var active [][]string
	last := time.Now()
	emptyCode := keccak(nil)
	t.walk(func(key, raw []byte) {
		check(db.ctx.Err())
		a := accountValue(raw)
		r.Counts["state_leaves_scanned"]++
		// Discover wrappers before resolving their address, so their embedded
		// identity/delegators can resolve missing address preimages too.
		if r.Shard == 0 && !bytes.Equal(a.code, emptyCode) {
			code, prefixed := loadCode(db, a.code)
			fullWrapper(db, key, code, prefixed, r, ids, &active)
		}
		addr, ok := ids.resolve(db, key)
		address := ""
		if ok {
			address = checksumAddress(addr)
		} else {
			r.Counts["missing_address_preimages"]++
		}
		accounts.write(hx(key), address, a.balance.String(), fmt.Sprint(a.nonce), hx(a.code), hx(a.root))
		if a.balance.Sign() > 0 {
			r.Counts["positive_liquid_accounts"]++
			totalLiquid.Add(totalLiquid, a.balance)
			liquid.write(hx(key), address, a.balance.String(), fmt.Sprint(a.nonce), hx(a.code))
			if !ok {
				r.Counts["unresolved_positive_accounts"]++
				unresolved.write(hx(key), a.balance.String())
			}
		}
		if time.Since(last) > 10*time.Second {
			accounts.flush()
			liquid.flush()
			unresolved.flush()
			fmt.Printf("full state shard=%d accounts=%d positive=%d validators=%d unresolved_positive=%d elapsed=%s\n", r.Shard, r.Counts["state_leaves_scanned"], r.Counts["positive_liquid_accounts"], r.Counts["validators_scanned"], r.Counts["unresolved_positive_accounts"], time.Since(start).Round(time.Second))
			last = time.Now()
		}
	})
	files := map[string]string{"accounts.csv": accounts.finish(), "liquid.csv": liquid.finish(), "unresolved-accounts.csv": unresolved.finish()}
	summary := map[string]any{"schema": "harmony-independent-full-state/v1", "status": "complete", "database_read_only": true, "shard": r.Shard, "cutoff": r.Cutoff, "manifest_sha256": r.ManifestSHA, "extractor_sha256": r.ExtractorSHA, "database": r.DB, "counts": r.Counts, "liquid_atto": totalLiquid.String(), "candidate_sources": discovery, "full_state_traversal": true, "complete_migration_balances": false, "limitations": []string{"this is one shard's raw state components, not final two-shard claims", "cross-shard pending transfers and reviewed delivery policy are not included", "unresolved addresses are retained by secure key; see unresolved-accounts.csv"}}
	if r.Shard == 0 {
		staking := newArtifact(dir, "staking.csv", []string{"secure_key", "address", "active_delegation_atto", "pending_undelegation_atto", "unclaimed_reward_atto"})
		defer staking.file.Close()
		split := newArtifact(dir, "staking-components.csv", []string{"secure_key", "address", "self_stake_atto", "delegated_atto", "pending_undelegation_atto", "unclaimed_reward_atto"})
		defer split.file.Close()
		addresses := sortedAddresses(r.Accounts)
		sort.Slice(addresses, func(i, j int) bool {
			return bytes.Compare(keccak(unhex(addresses[i], 20)), keccak(unhex(addresses[j], 20))) < 0
		})
		stakeTotal, pendingTotal, rewardTotal := new(big.Int), new(big.Int), new(big.Int)
		for _, addr := range addresses {
			row := r.Accounts[addr]
			staked := new(big.Int).Add(amount(row, "self_stake_atto"), amount(row, "delegated_atto"))
			pending, reward := amount(row, "pending_undelegation_atto"), amount(row, "unclaimed_reward_atto")
			stakeTotal.Add(stakeTotal, staked)
			pendingTotal.Add(pendingTotal, pending)
			rewardTotal.Add(rewardTotal, reward)
			if staked.Sign() == 0 && pending.Sign() == 0 && reward.Sign() == 0 {
				continue
			}
			a := unhex(addr, 20)
			staking.write(hx(keccak(a)), checksumAddress(a), staked.String(), pending.String(), reward.String())
			split.write(hx(keccak(a)), checksumAddress(a), row["self_stake_atto"], row["delegated_atto"], pending.String(), reward.String())
		}
		files["staking.csv"] = staking.finish()
		files["staking-components.csv"] = split.finish()
		ds := newArtifact(dir, "delegations.csv", []string{"validator_address", "validator_secure_key", "delegator_address", "delegator_secure_key", "staked_to_vault_atto", "is_self_delegation"})
		defer ds.file.Close()
		sort.Slice(active, func(i, j int) bool {
			a, b := strings.ToLower(active[i][0]), strings.ToLower(active[j][0])
			if a == b {
				return strings.ToLower(active[i][2]) < strings.ToLower(active[j][2])
			}
			return a < b
		})
		for _, row := range active {
			ds.write(row...)
		}
		files["delegations.csv"] = ds.finish()
		summary["active_delegation_atto"] = stakeTotal.String()
		summary["pending_undelegation_atto"] = pendingTotal.String()
		summary["unclaimed_reward_atto"] = rewardTotal.String()
		w, exists := readAccount(t, unhex(woneAddress, 20))
		require(exists && hx(w.code) == woneCodeHash, "unexpected WONE code at pinned cutoff")
		loadCode(db, w.code)
		storage := newArtifact(dir, "wone-storage.csv", []string{"secure_storage_key", "value_rlp_hex"})
		defer storage.file.Close()
		var holders [][]string
		holderTotal := new(big.Int)
		last = time.Now()
		(patricia{db, w.root}).walk(func(key, value []byte) {
			check(db.ctx.Err())
			r.Counts["wone_storage_leaves"]++
			storage.write(hx(key), hx(value))
			if addr, known := ids.slots[hashArray(key)]; known {
				v := integer(decode(value))
				require(v.BitLen() <= 256, "invalid WONE storage value")
				if v.Sign() > 0 {
					holderTotal.Add(holderTotal, v)
					whole, fraction := new(big.Int), new(big.Int)
					whole.QuoRem(v, new(big.Int).Exp(big.NewInt(10), big.NewInt(18), nil), fraction)
					holders = append(holders, []string{checksumAddress(addr[:]), v.String(), whole.String() + "." + strings.Repeat("0", 18-len(fraction.String())) + fraction.String()})
				}
			}
			if time.Since(last) > 10*time.Second {
				storage.flush()
				fmt.Printf("WONE storage leaves=%d resolved_holders=%d elapsed=%s\n", r.Counts["wone_storage_leaves"], len(holders), time.Since(start).Round(time.Second))
				last = time.Now()
			}
		})
		files["wone-storage.csv"] = storage.finish()
		ws := newArtifact(dir, "wone-holders.csv", []string{"address", "wone_balance_atto", "wone_balance"})
		defer ws.file.Close()
		sort.Slice(holders, func(i, j int) bool { return strings.ToLower(holders[i][0]) < strings.ToLower(holders[j][0]) })
		for _, row := range holders {
			ws.write(row...)
		}
		files["wone-holders.csv"] = ws.finish()
		summary["wone_resolved_holders"] = len(holders)
		summary["wone_resolved_atto"] = holderTotal.String()
		summary["wone_native_backing_atto"] = w.balance.String()
		summary["wone_sum_matches_pinned_backing"] = holderTotal.Cmp(w.balance) == 0
		summary["wone_coverage_note"] = "Balances read directly from the authenticated storage trie. Address candidates are hints only. If resolved holder sum differs from pinned backing, holder coverage is not proven; no complete WONE claim may be asserted."
	}
	check(db.ctx.Err())
	summary["files_sha256"] = files
	summary["elapsed_seconds"] = time.Since(start).Seconds()
	summary["database_reads"] = db.reads
	raw, e := json.MarshalIndent(summary, "", "  ")
	check(e)
	f, e := os.OpenFile(filepath.Join(dir, "summary.json.partial"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0640)
	check(e)
	_, e = f.Write(append(raw, '\n'))
	check(e)
	check(f.Sync())
	check(f.Close())
	check(os.Link(filepath.Join(dir, "summary.json.partial"), filepath.Join(dir, "summary.json")))
	check(os.Remove(filepath.Join(dir, "summary.json.partial")))
	fmt.Printf("full state components complete: %s\n", dir)
}
